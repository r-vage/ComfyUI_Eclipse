# Source-frame video joining, synchronized audio, and native VIDEO extraction.

import io as python_io
import json
import math
import os
import tempfile
import threading
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache

import av
import folder_paths
import numpy as np
import torch
from comfy.model_management import get_free_memory
from comfy_api.latest import InputImpl
from PIL import Image

from .video_helpers import TemporaryVideo
from .video_metadata import display_parameters, parse_video_parameters
from .video_timing import (
    VideoTiming,
    check_media_interrupt,
    encode_timed_video,
    join_timing,
    media_progress,
)

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".ts"}
MAX_CLIPS = 128
MAX_REPORT = 262144
_media_cache = OrderedDict()
_media_cache_lock = threading.Lock()


def _recall_media(key):
    with _media_cache_lock:
        entry = _media_cache.get(key)
        if entry is None:
            return None
        reference, encoded_timing = entry
        video = reference()
        if video is None:
            del _media_cache[key]
            return None
        _media_cache.move_to_end(key)
    return video, encoded_timing


def _remember_media(key, video, encoded_timing):
    # Reuse the joined video for GenData changes, without retaining files after
    # ComfyUI/preview consumers release them.
    with _media_cache_lock:
        _media_cache[key] = weakref.ref(video), encoded_timing
        _media_cache.move_to_end(key)
        while len(_media_cache) > 8:
            _media_cache.popitem(last=False)


def resolve_video_file(reference):
    if not isinstance(reference, dict) or reference.get("type") not in ("input", "output"):
        raise ValueError("Video reference must select input or output")
    filename, subfolder = reference.get("filename", ""), reference.get("subfolder", "")
    if not isinstance(filename, str) or not filename or any(c in filename for c in ("/", "\\", "\x00")):
        raise ValueError("Invalid video filename")
    if os.path.splitext(filename)[1].lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Unsupported video extension")
    if not isinstance(subfolder, str) or "\x00" in subfolder or "\\" in subfolder:
        raise ValueError("Invalid video subfolder")
    parts = subfolder.split("/") if subfolder else []
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Invalid video subfolder")
    root = folder_paths.get_input_directory() if reference["type"] == "input" else folder_paths.get_output_directory()
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, *parts, filename))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("Video must be inside the selected folder")
    return path


def parse_playlist(value):
    if not isinstance(value, str) or len(value) > 262144:
        raise ValueError("Playlist must be JSON text smaller than 256 KiB")
    try:
        manifest = json.loads(value)
    except ValueError as error:
        raise ValueError("Invalid playlist JSON") from error
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("clips"), list):
        raise ValueError("Expected playlist version 1 with a clips list")
    if len(manifest["clips"]) > MAX_CLIPS:
        raise ValueError(f"At most {MAX_CLIPS} clips are supported")
    rows, identities, selected = [], set(), 0
    for row in manifest["clips"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not 0 < len(row["id"]) <= 128 or row["id"] in identities:
            raise ValueError("Each clip needs a distinct stable id")
        identities.add(row["id"])
        item = {"id": row["id"], "file": row.get("file")}
        resolve_video_file(item["file"])
        for name in ("start_frame", "load_cap"):
            number = row.get(name, 0)
            if type(number) is not int or not 0 <= number <= 2**31 - 1:
                raise ValueError(f"{name} must be a nonnegative source-frame count")
            item[name] = number
        for name, default in (("enabled", True), ("mute", False), ("use_metadata", False)):
            flag = row.get(name, default)
            if type(flag) is not bool:
                raise ValueError(f"{name} must be a boolean")
            item[name] = flag
        if item["use_metadata"]:
            if not item["enabled"]:
                raise ValueError("A disabled clip cannot supply generation data")
            selected += 1
        rows.append(item)
    if selected > 1:
        raise ValueError("Select at most one generation-data source")
    return rows


def file_version(path):
    stat = os.stat(path)
    return stat.st_mtime_ns, stat.st_size


def _open(source):
    if hasattr(source, "seek"):
        source.seek(0)
    return av.open(source)


def _video_stream(container):
    if not container.streams.video:
        raise ValueError("File contains no video stream")
    stream = container.streams.video[0]
    if int(stream.codec_context.color_trc or 0) in (16, 18):
        raise ValueError("HDR conversion is not supported; provide an SDR video")
    return stream


def _geometry(frame, stream):
    if int(frame.color_trc or 0) in (16, 18):
        raise ValueError("HDR conversion is not supported; provide an SDR video")
    if any(component.is_alpha for component in frame.format.components):
        raise ValueError("Alpha video needs explicit compositing before loading")
    rotation = round(frame.rotation / 90) % 4 if frame.rotation else 0
    if frame.rotation and not math.isclose(frame.rotation % 90, 0, abs_tol=.01):
        raise ValueError("Only quarter-turn display rotation is supported")
    aspect = Fraction(stream.sample_aspect_ratio or 1)
    width, height = max(1, round(frame.width * aspect)), frame.height
    return (height, width, rotation, aspect) if rotation % 2 else (width, height, rotation, aspect)


def _pixels(frame, stream):
    _width, _height, rotation, aspect = _geometry(frame, stream)
    array = frame.to_ndarray(format="rgb24")
    if aspect != 1:
        array = np.asarray(Image.fromarray(array).resize((max(1, round(frame.width * aspect)), frame.height), Image.Resampling.LANCZOS))
    if rotation:
        array = np.rot90(array, k=rotation)
    return np.ascontiguousarray(array)


def probe_video(source):
    # Header + one displayed frame only; exact counts are resolved at execution.
    with _open(source) as container:
        stream = _video_stream(container)
        first = next(container.decode(stream), None)
        if first is None:
            raise ValueError("Video contains no decodable frames")
        width, height, rotation, _aspect = _geometry(first, stream)
        sound = next(iter(container.streams.audio), None)
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else (container.duration / av.time_base if container.duration else None)
        result = {"width": width, "height": height, "rotation": rotation * 90,
                  "duration": duration, "frame_count_estimate": stream.frames or None,
                  "fps": str(stream.average_rate) if stream.average_rate else None,
                  "codec": stream.codec_context.name, "container": container.format.name,
                  "pixel_format": first.format.name, "bit_depth": max(part.bits for part in first.format.components),
                  "color_transfer": int(first.color_trc or 0), "sample_aspect_ratio": str(stream.sample_aspect_ratio or 1),
                  "first_timestamp": float(first.pts * first.time_base) if first.pts is not None else None,
                  "audio": bool(sound), "sample_rate": sound.rate if sound else None,
                  "channels": sound.codec_context.channels if sound else None,
                  "parameters": display_parameters(parse_video_parameters(container.metadata.get("parameters")))}
    return result


@dataclass(frozen=True)
class FrameScan:
    times: tuple[Fraction, ...]
    durations: tuple[Fraction, ...]
    width: int
    height: int
    time_base: Fraction


def scan_video(source):
    times, durations = [], []
    dimensions = None
    with _open(source) as container:
        stream = _video_stream(container)
        time_base = stream.time_base
        progress = media_progress(stream.frames)
        end = (stream.start_time or 0) * stream.time_base + stream.duration * stream.time_base if stream.duration is not None else None
        for frame in container.decode(stream):
            check_media_interrupt()
            if frame.pts is None:
                raise ValueError("Video frame has no presentation timestamp")
            position = frame.pts * frame.time_base
            if times and position <= times[-1]:
                raise ValueError("Video timestamps must be strictly increasing")
            geometry = _geometry(frame, stream)[:2]
            if dimensions is not None and dimensions != geometry:
                raise ValueError("Video changes dimensions mid-stream")
            dimensions = geometry
            times.append(position)
            durations.append(frame.duration * frame.time_base if frame.duration else None)
            if progress:
                progress.update_absolute(len(times))
        if not times:
            raise ValueError("Video contains no frames")
        for index in range(len(times) - 1):
            durations[index] = times[index + 1] - times[index]
        if not durations[-1] and end is not None and end > times[-1]:
            durations[-1] = end - times[-1]
        if not durations[-1] or durations[-1] <= 0:
            raise ValueError("Cannot determine the last frame duration")
    return FrameScan(tuple(times), tuple(durations), *dimensions, time_base)


@lru_cache(maxsize=16)
def _cached_scan(path, version):
    return scan_video(path)


def _fit(array, width, height, fit):
    image = Image.fromarray(array)
    if image.size == (width, height):
        return array
    if fit == "stretch":
        return np.asarray(image.resize((width, height), Image.Resampling.LANCZOS))
    ratio = (max if fit == "crop" else min)(width / image.width, height / image.height)
    image = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
    if fit == "crop":
        left, top = (image.width - width) // 2, (image.height - height) // 2
        image = image.crop((left, top, left + width, top + height))
    else:
        canvas = Image.new("RGB", (width, height))
        canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
        image = canvas
    return np.asarray(image)


def extract_audio(source, start, duration, *, rate=48000, channels=2, sample_count=None):
    # Preserve absolute audio/video offsets. Empty or short tracks leave silence.
    count = round(duration * rate) if sample_count is None else sample_count
    data = np.zeros((channels, count), dtype=np.float32)
    with _open(source) as container:
        stream = next(iter(container.streams.audio), None)
        if stream is None:
            return {"waveform": torch.from_numpy(data)[None], "sample_rate": rate}
        resampler = av.AudioResampler(format="fltp", layout="stereo" if channels == 2 else "mono", rate=rate)
        next_position = None

        def place(frame):
            nonlocal next_position
            if frame.pts is not None:
                position = frame.pts * frame.time_base
            elif next_position is not None:
                position = next_position
            else:
                raise ValueError("Audio has no usable timestamps")
            next_position = position + Fraction(frame.samples, rate)
            offset = round((position - start) * rate)
            values = frame.to_ndarray()
            left, right = max(0, offset), min(count, offset + frame.samples)
            if right > left:
                data[:, left:right] = values[:, left - offset:right - offset]

        for frame in container.decode(stream):
            check_media_interrupt()
            for output in resampler.resample(frame):
                place(output)
            if next_position is not None and next_position >= start + duration:
                break
        for output in resampler.resample(None):
            place(output)
    return {"waveform": torch.from_numpy(data)[None], "sample_rate": rate}


def _join_audio(parts, durations):
    rate = 48000
    total = round(sum(durations, Fraction()) * rate)
    result = torch.zeros((1, 2, total), dtype=torch.float32)
    position = Fraction()
    for audio, duration in zip(parts, durations):
        start = round(position * rate)
        position += duration
        end = round(position * rate)
        if audio is not None:
            count = min(end - start, audio["waveform"].shape[-1])
            result[..., start:start + count] = audio["waveform"][..., :count]
    return {"waveform": result, "sample_rate": rate}


def _playlist_audio(clips):
    rate = 48000
    total = sum((clip.timing.duration for clip in clips), Fraction())
    result = torch.zeros((1, 2, round(total * rate)), dtype=torch.float32)
    position = Fraction()
    for clip in clips:
        check_media_interrupt()
        start = round(position * rate)
        position += clip.timing.duration
        end = round(position * rate)
        if not clip.row["mute"] and clip.info["audio"]:
            part = extract_audio(clip.path, clip.scan.times[clip.first], clip.timing.duration, sample_count=end - start)
            result[..., start:end] = part["waveform"]
    return {"waveform": result, "sample_rate": rate}


@dataclass
class ClipRange:
    row: dict
    path: str
    scan: FrameScan
    first: int
    stop: int
    info: dict

    @property
    def timing(self):
        return VideoTiming(self.scan.durations[self.first:self.stop])


def _frames_for_clips(clips, width, height, fit):
    for clip in clips:
        with _open(clip.path) as container:
            stream = _video_stream(container)
            for index, frame in enumerate(container.decode(stream)):
                check_media_interrupt()
                if index >= clip.stop:
                    break
                if index >= clip.first:
                    yield _fit(_pixels(frame, stream), width, height, fit)


def _fixed_frames(frames, timing, rate):
    iterator = iter(frames)
    current = next(iterator)
    end = timing.durations[0]
    index = 0
    count = math.ceil(timing.duration * rate)
    try:
        for output in range(count):
            moment = Fraction(output, 1) / rate
            while moment >= end and index + 1 < len(timing):
                current = next(iterator)
                index += 1
                end += timing.durations[index]
            yield current
    finally:
        if hasattr(iterator, "close"):
            iterator.close()


def load_playlist(playlist, width=0, height=0, fit="pad", timing_mode="source", fps=24.0):
    rows = parse_playlist(playlist)
    if fit not in ("pad", "crop", "stretch") or timing_mode not in ("source", "fixed"):
        raise ValueError("Invalid fit or timing selection")
    if any(type(value) is not int or not 0 <= value <= 16384 for value in (width, height)):
        raise ValueError("Width and height must be between 0 and 16384")
    clips, report, generation_data = [], [], None
    for index, row in enumerate(rows):
        path = resolve_video_file(row["file"])
        report.append(f"Clip {index + 1}: {row['file']['filename']}\nRow: {row['id']} | enabled={row['enabled']} mute={row['mute']} use_metadata={row['use_metadata']}")
        if not row["enabled"]:
            report.append("Disabled; not decoded.")
            continue
        try:
            scan = _cached_scan(path, file_version(path))
            info = probe_video(path)
        except (OSError, ValueError, av.error.FFmpegError) as error:
            raise ValueError(f"{row['file']['filename']}: {error}") from error
        first = row["start_frame"]
        stop = min(len(scan.times), first + row["load_cap"]) if row["load_cap"] else len(scan.times)
        if first >= len(scan.times):
            raise ValueError(f"{row['file']['filename']}: start frame {first} is beyond the last frame")
        clip = ClipRange(row, path, scan, first, stop, info)
        clips.append(clip)
        parameters = parse_video_parameters(info["parameters"]["raw"])
        parameters["status"] = info["parameters"]["status"]
        if row["use_metadata"]:
            generation_data = parameters["data"]
        report.append(json.dumps({key: value for key, value in info.items() if key != "parameters"}, ensure_ascii=False))
        report.append(f"Source frames [{first}, {stop}); start={float(scan.times[first]):.9f}s duration={float(clip.timing.duration):.9f}s; frames={stop - first}; {'variable' if len(set(clip.timing.durations)) > 1 else 'constant'} timing")
        report.append(parameters["status"] + "\nSampling data: " + json.dumps(display_parameters(parameters)["data"], ensure_ascii=False))
        report.append("Original A1111 text (resource fields are not reused):\n" + (parameters["raw"] or "(none)"))
    if not clips:
        raise ValueError("Add and enable at least one video")
    source_width, source_height = clips[0].scan.width, clips[0].scan.height
    if width == height == 0:
        width, height = source_width, source_height
    elif width == 0:
        width = max(1, round(height * source_width / source_height))
    elif height == 0:
        height = max(1, round(width * source_height / source_width))
    if max(width, height) > 16384:
        raise ValueError("Resolved dimensions exceed 16384")
    timing = join_timing((clip.row["id"], clip.timing) for clip in clips)
    audio_bytes = round(timing.duration * 48000) * 2 * 4
    key = (tuple((clip.path, file_version(clip.path), clip.row["id"], clip.first, clip.stop, clip.row["mute"]) for clip in clips),
           width, height, fit, timing_mode, str(fps) if timing_mode == "fixed" else None)
    cached = _recall_media(key)
    if cached is not None:
        video, encoded_timing = cached
    else:
        # Frames stream to disk; account for joined PCM, one clip and decoder scratch.
        required_bytes = 2 * audio_bytes + width * height * 12
        available_bytes = get_free_memory(torch.device("cpu"))
        if required_bytes > available_bytes * .8:
            raise ValueError(f"Joining needs about {required_bytes / 2**30:.2f} GiB of RAM for audio and frame processing; {available_bytes / 2**30:.2f} GiB is available. Reduce size/load caps.")
        audio = _playlist_audio(clips)
        encoded_timing = timing
        frames = _frames_for_clips(clips, width, height, fit)
        if timing_mode == "fixed":
            if not math.isfinite(float(fps)) or not 1 <= float(fps) <= 240:
                raise ValueError("Fixed FPS must be between 1 and 240")
            rate = Fraction(str(fps))
            frames = _fixed_frames(frames, timing, rate)
            encoded_timing = VideoTiming.constant(math.ceil(timing.duration * rate), rate)
        descriptor, path = tempfile.mkstemp(prefix="EclipseLoadVideo_", suffix=".mp4", dir=folder_paths.get_temp_directory())
        os.close(descriptor)
        try:
            encode_timed_video(frames, encoded_timing, audio, path, width=width, height=height, crf=18, preset="veryfast")
            video = TemporaryVideo(path)
        except BaseException:
            os.unlink(path)
            raise
        _remember_media(key, video, encoded_timing)
    report.append(f"Result: {width}x{height}, fit={fit}, {len(timing)} source frames, {float(timing.duration):.9f}s.")
    report.append("Frames streamed to VIDEO. Audio: first track per file, 48000 Hz stereo; missing or muted intervals are silence.")
    if width % 2 or height % 2:
        report.append(f"Encoded VIDEO pads edges to {width + width % 2}x{height + height % 2} for codec alignment.")
    report.append(f"VIDEO: {timing_mode}, {len(encoded_timing)} frames, {float(encoded_timing.duration):.9f}s.")
    text = "\n\n".join(report)
    if len(text) > MAX_REPORT:
        text = text[:MAX_REPORT] + "\n[Report truncated]"
    return video, generation_data, float(encoded_timing.duration), text


def split_video(video):
    if isinstance(video, InputImpl.VideoFromList):
        parts = [split_video(item) for item in video.videos]
        shapes = {tuple(part[0].shape[1:]) for part in parts}
        if len(shapes) != 1:
            raise ValueError("Concatenated VIDEO frames have different dimensions")
        images = torch.cat([part[0] for part in parts])
        timing = join_timing((index, part[2]) for index, part in enumerate(parts))
        # Normalize component audio through the same timeline alignment path.
        audio_parts = [_normalize_component_audio(part[1], part[2].duration) for part in parts]
        audio = _join_audio(audio_parts, [part[2].duration for part in parts]) if any(part[1] is not None for part in parts) else None
        if video.complete_audio is not None:
            audio = _normalize_component_audio(video.complete_audio, timing.duration)
        return images, audio, timing, f"Native concatenation: {len(parts)} clips, {len(timing)} frames, {float(timing.duration):.9f}s"
    if isinstance(video, InputImpl.VideoFromFile):
        source = video.get_stream_source()
        scan = scan_video(source)
        start_time, duration = video.get_active_trim_window()
        # Match native trim's integer tick boundaries, including float rounding.
        start = int(start_time / scan.time_base) * scan.time_base
        end = int((start_time + duration) / scan.time_base) * scan.time_base if duration else None
        indices = [index for index, moment in enumerate(scan.times) if moment >= start and (end is None or moment < end)]
        if not indices:
            raise ValueError("VIDEO trim contains no frames")
        durations = list(scan.durations[indices[0]:indices[-1] + 1])
        if end is not None:
            durations[-1] = min(durations[-1], end - scan.times[indices[-1]])
        timing = VideoTiming(tuple(durations))
        width, height = video.get_dimensions()
        required_bytes = len(timing) * width * height * 3 * 4 * 2
        if required_bytes > get_free_memory(torch.device("cpu")) * .8:
            raise ValueError(f"Splitting this VIDEO needs about {required_bytes / 2**30:.2f} GiB of RAM. Trim or reduce the video before splitting.")
        # Public component extraction honors native crop and display rotation.
        components = video.get_components()
        timing.validate_count(len(components.images))
        if components.alpha is not None:
            raise ValueError("Alpha video needs explicit compositing before splitting")
        info = probe_video(source)
        audio = extract_audio(source, scan.times[indices[0]], timing.duration) if info["audio"] else None
        return components.images, audio, timing, f"File VIDEO: {len(timing)} frames, {float(timing.duration):.9f}s; source timestamps retained"
    if isinstance(video, InputImpl.VideoFromComponents):
        components = video.get_components()
        if components.alpha is not None or video.get_color_space() not in ("sRGB", "auto"):
            raise ValueError("Splitter requires SDR RGB video")
        timing = VideoTiming.constant(len(components.images), components.frame_rate)
        return components.images, components.audio, timing, f"Component VIDEO: {len(timing)} frames at {components.frame_rate} FPS"
    raise ValueError("This VIDEO implementation has no qualified timing adapter; use a native file, component, or concatenated VIDEO")


def _normalize_component_audio(audio, duration):
    if audio is None:
        return None
    data = audio["waveform"].detach().cpu().float()
    if data.ndim != 3 or data.shape[0] != 1 or data.shape[1] not in (1, 2):
        raise ValueError("Expected mono/stereo AUDIO with batch size one")
    # AV resampling keeps the actual PCM sample rate; never changes tempo.
    rate = int(audio["sample_rate"])
    if rate <= 0:
        raise ValueError("Audio sample rate must be positive")
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    result = np.zeros((2, round(duration * 48000)), dtype=np.float32)
    offset = 0
    for start in range(0, data.shape[-1], 4096):
        check_media_interrupt()
        frame = av.AudioFrame.from_ndarray(data[0, :, start:start + 4096].numpy(), format="fltp", layout="mono" if data.shape[1] == 1 else "stereo")
        frame.sample_rate = rate
        for output in resampler.resample(frame):
            count = min(output.samples, result.shape[-1] - offset)
            if count > 0:
                result[:, offset:offset + count] = output.to_ndarray()[:, :count]
                offset += count
    for output in resampler.resample(None):
        count = min(output.samples, result.shape[-1] - offset)
        if count > 0:
            result[:, offset:offset + count] = output.to_ndarray()[:, :count]
            offset += count
    return {"waveform": torch.from_numpy(result)[None], "sample_rate": 48000}


def video_thumbnail(path):
    with _open(path) as container:
        stream = _video_stream(container)
        frame = next(container.decode(stream), None)
        if frame is None:
            raise ValueError("Video has no poster frame")
        poster = Image.fromarray(_pixels(frame, stream))
        poster.thumbnail((240, 160), Image.Resampling.LANCZOS)
        buffer = python_io.BytesIO()
        poster.save(buffer, "WEBP", quality=80)
        return buffer.getvalue()
