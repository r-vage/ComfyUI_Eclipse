# Exact, execution-local frame storage. Descriptors own files, never tensors or mappings.
import errno
import math
import os
import re
import tempfile
from dataclasses import dataclass

import numpy as np
import torch

TIMELINE_TYPE = "ECLIPSE_FRAME_TIMELINE"


def raise_storage_error(error, directory):
    # NumPy tofile can report a short write without preserving the OS errno.
    if not isinstance(error, OSError):
        return
    short_write = error.errno is None and re.fullmatch(r"\d+ requested and \d+ written", str(error))
    if (error.errno not in (errno.ENOSPC, getattr(errno, "EDQUOT", -1))
            and getattr(error, "winerror", None) not in (39, 112) and not short_write):
        return
    raise OSError(
        error.errno,
        f"Eclipse temporary video storage is full, over quota, or could not complete a write: {error}. "
        f"Temporary folder: {os.path.abspath(directory)}. "
        "Free space on this drive. For manual cleanup, finish or cancel the job, then stop ComfyUI. "
        "In this temporary folder, remove unneeded EclipseFrames_* folders (exact cached frames) "
        "and EclipseVideo_temp_*.mp4 files (accumulated previews). "
        "Deleting frame chunks means regeneration is required for later caption changes or re-exports; "
        "a preview MP4 cannot replace them. Keep final videos in output, source files in input, and models. "
        "Restart ComfyUI before queueing again. See Readme/Frame_Timeline.md for cleanup details.",
    ) from error


def check_interrupted():
    from comfy.model_management import throw_exception_if_processing_interrupted

    throw_exception_if_processing_interrupted()


@dataclass(frozen=True)
class FrameChunk:
    owner: tempfile.TemporaryDirectory
    start: int
    count: int

    @property
    def path(self):
        return os.path.join(self.owner.name, "frames.npy")


@dataclass(frozen=True)
class FrameTimeline:
    chunks: tuple[FrameChunk, ...]
    height: int
    width: int
    channels: int
    dtype: str
    fps: float

    def __deepcopy__(self, memo):
        # Immutable graph copies share the same file owners.
        memo[id(self)] = self
        return self

    def __len__(self):
        return sum(chunk.count for chunk in self.chunks)

    @property
    def shape(self):
        return (len(self), self.height, self.width, self.channels)

    def __iter__(self):
        for chunk in self.chunks:
            mapping = np.load(chunk.path, mmap_mode="r", allow_pickle=False)
            try:
                for index in range(chunk.start, chunk.start + chunk.count):
                    check_interrupted()
                    # A detached frame cannot retain or outlive an open mapping.
                    frame = torch.from_numpy(np.array(mapping[index], copy=True))
                    yield frame.view(torch.bfloat16) if self.dtype == "bfloat16" else frame
            finally:
                mapping._mmap.close()

    def slice(self, start=0, count=None):
        count = len(self) - start if count is None else count
        if start < 0 or count < 1 or start + count > len(self):
            raise ValueError("Timeline range must be nonempty and within stored frames.")
        chunks = []
        offset = 0
        for chunk in self.chunks:
            first = max(start - offset, 0)
            last = min(start + count - offset, chunk.count)
            if last > first:
                chunks.append(FrameChunk(chunk.owner, chunk.start + first, last - first))
            offset += chunk.count
        return FrameTimeline(tuple(chunks), self.height, self.width, self.channels, self.dtype, self.fps)

    def tail(self, count):
        return torch.stack(list(self.slice(len(self) - count, count)))


def append_frames(images, fps, previous=None, *, directory=None):
    if not isinstance(images, torch.Tensor) or images.ndim != 4 or min(images.shape) < 1:
        raise ValueError("Timeline requires a nonempty NHWC IMAGE batch.")
    if images.dtype not in (torch.float16, torch.bfloat16, torch.float32, torch.float64):
        raise ValueError("Exact storage requires float16, bfloat16, float32 or float64 pixels.")
    if images.shape[-1] not in (1, 3, 4) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid timeline channels or FPS.")
    shape = tuple(images.shape)
    dtype = str(images.dtype).removeprefix("torch.")
    storage_dtype = "uint16" if dtype == "bfloat16" else dtype
    if previous is not None:
        if not isinstance(previous, FrameTimeline):
            raise TypeError("previous must be an Eclipse frame timeline.")
        if (*previous.shape[1:], previous.dtype, previous.fps) != (*shape[1:], dtype, fps):
            raise ValueError("Timeline dimensions, dtype and FPS must remain unchanged.")
    if directory is None:
        import folder_paths

        directory = folder_paths.get_temp_directory()
    owner = None
    try:
        owner = tempfile.TemporaryDirectory(prefix="EclipseFrames_", dir=directory)
        path = os.path.join(owner.name, "frames.npy")
        # Write sequentially without mapping dirty pages or copying a full CPU scene.
        with open(path, "wb") as stream:
            np.lib.format.write_array_header_2_0(stream, {
                "descr": np.dtype(storage_dtype).str, "fortran_order": False, "shape": shape,
            })
            for frame in images:
                check_interrupted()
                pixels = frame.detach().cpu().contiguous()
                if dtype == "bfloat16":
                    pixels = pixels.view(torch.uint16)
                pixels.numpy().tofile(stream)
        chunk = FrameChunk(owner, 0, shape[0])
        return FrameTimeline((previous.chunks if previous else ()) + (chunk,), *shape[1:], dtype, float(fps))
    except BaseException as error:
        if owner is not None:
            owner.cleanup()
        raise_storage_error(error, directory)
        raise


def trim_timeline_audio(timeline, audio, mode):
    if mode not in ("none", "video_to_audio", "audio_to_video", "shortest"):
        raise ValueError("Frame timelines support duration trimming, not loop matching/blending.")
    if audio is not None and mode != "none":
        rate = int(audio["sample_rate"])
        samples = audio["waveform"].shape[-1]
        if mode in ("video_to_audio", "shortest"):
            timeline = timeline.slice(0, min(len(timeline), max(1, math.floor(samples / rate * timeline.fps))))
        if mode in ("audio_to_video", "shortest"):
            audio = {**audio, "waveform": audio["waveform"][..., :max(1, round(len(timeline) / timeline.fps * rate))]}
    return timeline, audio


def timeline_input(value):
    # MatchType list inputs must never silently drop a timeline beside an IMAGE.
    pending = [value]
    timelines = []
    other = False
    while pending:
        item = pending.pop()
        if isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, FrameTimeline):
            timelines.append(item)
        else:
            other = True
    if timelines and (len(timelines) != 1 or other):
        raise ValueError("Connect one timeline; mixed timeline/IMAGE/VIDEO collections are unsupported.")
    return timelines[0] if timelines else None
