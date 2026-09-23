"""Shared PyAV audio decoding helpers for Eclipse audio features."""

from __future__ import annotations

import os
import tempfile
import wave

import av  # type: ignore
import folder_paths  # type: ignore
import torch  # type: ignore

from .logger import log

_LOG_PREFIX = "Audio"


def save_audio_preview(waveform: torch.Tensor, sample_rate: int) -> dict:
    """Save the full first batch item as temporary PCM for browser audition.

    Only this preview is quantized; the tensor supplied downstream is untouched.
    ComfyUI owns the temp directory and its normal session cleanup.
    """
    with tempfile.NamedTemporaryFile(
        prefix="eclipse_audio_", suffix=".wav",
        dir=folder_paths.get_temp_directory(), delete=False,
    ) as file:
        path = file.name
    try:
        pcm = waveform[0].detach().to(device="cpu", dtype=torch.float32)
        pcm = (pcm.clamp(-1, 1) * 32767).to(torch.int16).t().contiguous()
        with wave.open(path, "wb") as preview:
            preview.setnchannels(waveform.shape[1])
            preview.setsampwidth(2)
            preview.setframerate(int(sample_rate))
            preview.writeframes(pcm.numpy().tobytes())
    except Exception:
        os.unlink(path)
        raise
    return {"filename": os.path.basename(path), "subfolder": "", "type": "temp"}


def _f32_pcm(waveform: torch.Tensor) -> torch.Tensor:
    """Convert decoded PCM to float32 samples in the range [-1, 1]."""
    if waveform.dtype.is_floating_point:
        return waveform.float()
    if waveform.dtype == torch.int16:
        return waveform.float() / (2**15)
    if waveform.dtype == torch.int32:
        return waveform.float() / (2**31)
    raise ValueError(f"Unsupported waveform dtype: {waveform.dtype}")


def load_audio_file(
    filepath: str,
    start_time: float = 0.0,
    duration: float = 0.0,
) -> tuple[torch.Tensor, int]:
    """Decode an audio file to ``[channels, samples]`` float32 PCM."""
    with av.open(filepath) as audio_file:
        if not audio_file.streams.audio:
            raise ValueError("No audio stream found in the file.")

        stream = audio_file.streams.audio[0]
        sample_rate = stream.codec_context.sample_rate
        channel_count = stream.channels
        if not sample_rate:
            raise ValueError("Audio stream has no sample rate.")

        start_time = max(0.0, start_time)
        duration = max(0.0, duration)
        if start_time > 0.0:
            try:
                audio_file.seek(int(start_time * av.time_base))
            except Exception as error:  # noqa: BLE001 - decoder seek fallback
                log.warning(
                    _LOG_PREFIX,
                    f"Seek failed ({type(error).__name__}); decoding from start.",
                )

        start_sample = round(start_time * sample_rate)
        max_samples: int | None = (
            round(duration * sample_rate) if duration > 0.0 else None
        )
        frames: list[torch.Tensor] = []
        total_samples = 0
        for frame in audio_file.decode(streams=stream.index):
            if not isinstance(frame, av.AudioFrame):
                continue
            buffer = torch.from_numpy(frame.to_ndarray())
            if buffer.shape[0] != channel_count:
                buffer = buffer.view(-1, channel_count).t()

            if (
                start_time > 0.0
                and frame.pts is not None
                and frame.time_base is not None
            ):
                frame_start_sample = int(
                    float(frame.pts) * float(frame.time_base) * sample_rate
                )
                if frame_start_sample < start_sample:
                    skip = start_sample - frame_start_sample
                    if skip >= buffer.shape[1]:
                        continue
                    buffer = buffer[:, skip:]

            frames.append(buffer)
            total_samples += buffer.shape[1]
            if max_samples is not None and total_samples >= max_samples:
                break

        if not frames:
            raise ValueError("No audio frames decoded.")

        waveform = torch.cat(frames, dim=1)
        if max_samples is not None and waveform.shape[1] > max_samples:
            waveform = waveform[:, :max_samples]
        return _f32_pcm(waveform), sample_rate
