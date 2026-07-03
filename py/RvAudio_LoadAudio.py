# Load Audio [Eclipse]
#
# Drop-in replacement for ComfyUI's built-in LoadAudio node with two extra
# controls — `start_time` and `duration` (in seconds) — similar to the VHS
# LoadAudioUpload node. Uses PyAV directly (already an Eclipse dependency)
# and seeks before decoding so trimming long files is efficient.
#
# - start_time = 0 → start at file beginning
# - duration  = 0 → load until end-of-file

import os
import hashlib
from typing import Optional

import av  # type: ignore
import torch  # type: ignore
import folder_paths  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "LoadAudio"


def _f32_pcm(wav: torch.Tensor) -> torch.Tensor:
    # Convert audio to float32 PCM in [-1, 1].
    if wav.dtype.is_floating_point:
        return wav
    if wav.dtype == torch.int16:
        return wav.float() / (2 ** 15)
    if wav.dtype == torch.int32:
        return wav.float() / (2 ** 31)
    raise ValueError(f"Unsupported wav dtype: {wav.dtype}")


def _load_trimmed(filepath: str, start_time: float = 0.0, duration: float = 0.0):
    # Decode an audio file with optional start offset and duration cap.
    # Returns (waveform[C, T] float32, sample_rate).
    with av.open(filepath) as af:
        if not af.streams.audio:
            raise ValueError("No audio stream found in the file.")

        stream = af.streams.audio[0]
        sr = stream.codec_context.sample_rate
        n_channels = stream.channels

        start_time = max(0.0, start_time)
        duration = max(0.0, duration)

        if start_time > 0.0:
            try:
                # av.time_base is AV_TIME_BASE (1_000_000). Seek lands on a
                # keyframe at or before the requested offset; we trim the
                # leading samples below.
                af.seek(int(start_time * av.time_base))
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Seek failed ({e}); decoding from start.")

        start_sample = round(start_time * sr)
        max_samples: Optional[int] = round(duration * sr) if duration > 0.0 else None

        frames = []
        total = 0
        for frame in af.decode(streams=stream.index):
            if not isinstance(frame, av.AudioFrame):
                continue
            buf = torch.from_numpy(frame.to_ndarray())
            if buf.shape[0] != n_channels:
                buf = buf.view(-1, n_channels).t()

            # Trim leading samples if the seek landed before start_time.
            if start_time > 0.0 and frame.pts is not None and frame.time_base is not None:
                pts = float(frame.pts)
                tb = float(frame.time_base)
                frame_start_sample = int(pts * tb * sr)
                if frame_start_sample < start_sample:
                    skip = start_sample - frame_start_sample
                    if skip >= buf.shape[1]:
                        continue
                    buf = buf[:, skip:]

            frames.append(buf)
            total += buf.shape[1]
            if max_samples is not None and total >= max_samples:
                break

        if not frames:
            raise ValueError("No audio frames decoded.")

        wav = torch.cat(frames, dim=1)
        if max_samples is not None and wav.shape[1] > max_samples:
            wav = wav[:, :max_samples]
        return _f32_pcm(wav), sr


class RvAudio_LoadAudio(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        input_dir = folder_paths.get_input_directory()
        try:
            files = folder_paths.filter_files_content_types(os.listdir(input_dir), ["audio", "video"])
        except Exception:
            files = []
        return io.Schema(
            node_id="Load Audio [Eclipse]",
            display_name="Load Audio",
            category=CATEGORY.MAIN.value + CATEGORY.AUDIO.value,
            inputs=[
                io.Combo.Input(
                    "audio",
                    options=sorted(files),
                    tooltip="Audio (or video) file from ComfyUI's input folder. Drop files into input/ to make them appear here.",
                ),
                io.Float.Input(
                    "start_time",
                    default=0.0, min=0.0, max=86400.0, step=0.01,
                    tooltip="Offset from the start of the file (seconds). 0 = beginning.",
                ),
                io.Float.Input(
                    "duration",
                    default=0.0, min=0.0, max=86400.0, step=0.01,
                    tooltip="Maximum duration to load (seconds). 0 = load to end of file.",
                ),
            ],
            outputs=[
                io.Audio.Output("audio"),
                io.Float.Output("duration"),
            ],
        )

    @classmethod
    def execute(cls, audio, start_time: float = 0.0, duration: float = 0.0) -> io.NodeOutput:
        audio_path = folder_paths.get_annotated_filepath(audio)
        waveform, sample_rate = _load_trimmed(audio_path, start_time=start_time, duration=duration)
        out = {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}
        loaded_duration = float(waveform.shape[1]) / float(sample_rate) if sample_rate else 0.0
        return io.NodeOutput(out, loaded_duration)

    @classmethod
    def fingerprint_inputs(cls, audio, start_time: float = 0.0, duration: float = 0.0):
        audio_path = folder_paths.get_annotated_filepath(audio)
        m = hashlib.sha256()
        try:
            with open(audio_path, "rb") as f:
                m.update(f.read())
        except Exception:
            pass
        m.update(f"|{start_time:.6f}|{duration:.6f}".encode("utf-8"))
        return m.digest().hex()

    @classmethod
    def validate_inputs(cls, audio, **_kwargs):
        if not folder_paths.exists_annotated_filepath(audio):
            return f"Invalid audio file: {audio}"
        return True
