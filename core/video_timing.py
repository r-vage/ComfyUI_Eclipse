# Per-frame presentation timing shared by video extraction and saving.

import json
from contextvars import ContextVar
from dataclasses import dataclass
from fractions import Fraction
from itertools import accumulate
from math import lcm

import av
import numpy as np
import torch
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar

TIMING_TYPE = "ECLIPSE_VIDEO_TIMING"
media_cancellation = ContextVar("eclipse_video_cancellation", default=None)


def check_media_interrupt():
    cancellation = media_cancellation.get()
    if cancellation is not None:
        if cancellation.is_set():
            raise InterruptedError("Video processing cancelled")
        return
    throw_exception_if_processing_interrupted()


def media_progress(total):
    # Explicit browser preview jobs must not update a queued node's progress bar.
    return ProgressBar(total) if total and media_cancellation.get() is None else None


@dataclass(frozen=True)
class VideoTiming:
    durations: tuple[Fraction, ...]
    # Each span is (clip identity, first output frame, number of frames).
    spans: tuple[tuple[str, int, int], ...] = ()
    version: int = 1

    def __post_init__(self):
        if self.version != 1 or not self.durations:
            raise ValueError("Unsupported or empty video timing")
        if any(not isinstance(value, Fraction) or value <= 0 for value in self.durations):
            raise ValueError("Frame durations must be positive rational values")

    def __len__(self):
        return len(self.durations)

    @property
    def duration(self):
        return sum(self.durations, Fraction())

    @property
    def timestamps(self):
        return tuple(accumulate(self.durations, initial=Fraction()))[:-1]

    @classmethod
    def constant(cls, count, fps):
        rate = Fraction(str(fps))
        if rate <= 0 or count < 1:
            raise ValueError("Frame count and FPS must be positive")
        return cls((1 / rate,) * count)

    def validate_count(self, count):
        if count != len(self):
            raise ValueError(f"Timing describes {len(self)} frames, but received {count}. Frame selection/reordering requires matching timing and audio.")

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self


def join_timing(parts):
    durations, spans = [], []
    for identity, timing in parts:
        spans.append((str(identity), len(durations), len(timing)))
        durations.extend(timing.durations)
    return VideoTiming(tuple(durations), tuple(spans))


def _rgb_array(frame):
    if isinstance(frame, torch.Tensor):
        frame = frame.detach().cpu().numpy()
    frame = np.asarray(frame)
    if frame.ndim == 4 and frame.shape[0] == 1:
        frame = frame[0]
    if frame.ndim != 3 or frame.shape[-1] != 3:
        raise ValueError("Timed video encoding requires RGB frames")
    if frame.dtype != np.uint8:
        frame = np.clip(frame * 255, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(frame)


def encode_timed_video(frames, timing, audio, path, *, width, height,
                       codec="libx264", crf=18, preset="medium", metadata=None,
                       lossless=False):
    # Stream pixels; only the explicitly requested IMAGE output allocates a batch.
    if not isinstance(timing, VideoTiming):
        raise TypeError("Expected Eclipse video timing")
    denominator = lcm(*(value.denominator for value in timing.durations))
    if denominator > 2_000_000_000:
        raise ValueError("Source timestamps need an unsupported encoder time base")
    time_base = Fraction(1, denominator)
    encoded_width, encoded_height = (width, height) if lossless else (width + width % 2, height + height % 2)
    container = av.open(str(path), "w", options={"movflags": "use_metadata_tags+faststart"})
    iterator = iter(frames)
    try:
        for key, value in (metadata or {}).items():
            container.metadata[key] = value if isinstance(value, str) else json.dumps(value)
        stream = container.add_stream("libx264rgb" if lossless else codec, rate=Fraction(len(timing), 1) / timing.duration)
        stream.width, stream.height = encoded_width, encoded_height
        stream.pix_fmt = "rgb24" if lossless else "yuv420p"
        stream.codec_context.time_base = time_base
        stream.codec_context.max_b_frames = 0
        stream.options = {"crf": "0" if lossless else str(crf), "preset": preset}
        if lossless:
            # A bounded encoder queue matters as much as bounded inference batches.
            stream.options["tune"] = "zerolatency"
            stream.codec_context.thread_count = 2
        sound = None
        waveform = None
        rate = 0
        samples_written = 0
        if audio is not None:
            waveform = audio["waveform"].detach().cpu().float()
            rate = int(audio["sample_rate"])
            if waveform.ndim != 3 or waveform.shape[0] != 1 or waveform.shape[1] not in (1, 2) or rate < 1:
                raise ValueError("Audio must have shape [1, 1 or 2, samples] and a positive sample rate")
            waveform = waveform[0]
            sound = container.add_stream("pcm_f32le" if lossless else "aac", rate=rate)
            sound.layout = "mono" if waveform.shape[0] == 1 else "stereo"

        def write_audio(until):
            nonlocal samples_written
            if sound is None:
                return
            target = round(until * rate)
            while samples_written < target:
                check_media_interrupt()
                count = min(4096, target - samples_written)
                data = np.zeros((waveform.shape[0], count), dtype=np.float32)
                available = max(0, min(count, waveform.shape[-1] - samples_written))
                if available:
                    data[:, :available] = waveform[:, samples_written:samples_written + available].numpy()
                frame = av.AudioFrame.from_ndarray(data, format="fltp", layout=sound.layout.name)
                frame.sample_rate, frame.time_base, frame.pts = rate, Fraction(1, rate), samples_written
                samples_written += count
                for packet in sound.encode(frame):
                    container.mux(packet)

        packet_durations = {}

        def mux_video(packets):
            for packet in packets:
                packet.duration = packet_durations.pop(packet.pts, 0)
                container.mux(packet)

        position = Fraction()
        progress = media_progress(len(timing))
        for index, duration in enumerate(timing.durations):
            check_media_interrupt()
            try:
                array = _rgb_array(next(iterator))
            except StopIteration as error:
                raise ValueError("Fewer images than timing entries") from error
            if array.shape[:2] != (height, width):
                raise ValueError("All frames must match the selected output size")
            if (encoded_height, encoded_width) != (height, width):
                array = np.pad(array, ((0, height % 2), (0, width % 2), (0, 0)), mode="edge")
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            frame.time_base = time_base
            frame.pts = int(position / time_base)
            frame.duration = int(duration / time_base)
            packet_durations[frame.pts] = frame.duration
            mux_video(stream.encode(frame))
            position += duration
            write_audio(position)
            if progress:
                progress.update_absolute(index + 1)
        if next(iterator, None) is not None:
            raise ValueError("More images than timing entries")
        mux_video(stream.encode())
        if sound is not None:
            for packet in sound.encode():
                container.mux(packet)
    finally:
        close = getattr(iterator, "close", None)
        if close:
            close()
        container.close()
