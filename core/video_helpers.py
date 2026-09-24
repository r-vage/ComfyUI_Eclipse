# Shared preparation for video output nodes.

import math
import os
import tempfile
import weakref
from fractions import Fraction

import folder_paths
import torch
from comfy_api.latest import InputImpl, Types


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


class TemporaryVideo(InputImpl.VideoFromFile):
    def __init__(self, path):
        super().__init__(path)
        self._cleanup = weakref.finalize(self, self._remove, path)

    @staticmethod
    def _remove(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def preview_video_file(video):
    # Reuse Eclipse's owned, untrimmed render file. Other VIDEO implementations
    # export through the public streaming API, which honors trim/crop views.
    if not isinstance(video, TemporaryVideo):
        fd, path = tempfile.mkstemp(prefix="EclipsePreview_", suffix=".mp4", dir=folder_paths.get_temp_directory())
        os.close(fd)
        try:
            video.save_to(path, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
            video = TemporaryVideo(path)
        except BaseException:
            TemporaryVideo._remove(path)
            raise
    return video, {
        "filename": os.path.basename(video.get_stream_source()),
        "subfolder": "",
        "type": "temp",
        "format": "video/mp4",
        "frame_rate": float(video.get_frame_rate()),
    }
