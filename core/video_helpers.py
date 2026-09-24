# Shared preparation for video output nodes.

import math
from fractions import Fraction

import torch


def expand_still_image_for_audio(
    frames: list[torch.Tensor], fps: float, audio: object
) -> torch.Tensor | None:
    # Expand a single image as a view, so even long songs share one frame's storage.
    # Use the encoder's frame rate and round up to cover the complete audio.
    if len(frames) != 1 or not isinstance(audio, dict):
        return None
    image = frames[0]
    waveform = audio.get("waveform")
    if (
        image.ndim != 4
        or image.shape[0] != 1
        or not isinstance(waveform, torch.Tensor)
        or waveform.ndim not in (2, 3)
        or waveform.numel() == 0
    ):
        return None
    try:
        sample_rate = int(audio.get("sample_rate", 0))
        rate = Fraction(round(fps * 1000), 1000)
    except (TypeError, ValueError, OverflowError):
        return None
    if sample_rate <= 0 or rate <= 0:
        return None
    frame_count = max(1, math.ceil(Fraction(waveform.shape[-1], sample_rate) * rate))
    return image.expand(frame_count, -1, -1, -1)
