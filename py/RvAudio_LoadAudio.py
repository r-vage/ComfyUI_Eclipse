# Load Audio [Eclipse]
#
# File or incoming-AUDIO excerpts with seconds-based trimming, full-source
# audition, and an optional review stop. File decoding seeks through PyAV;
# incoming tensors are sliced at their native rate without modifying samples.
#
# - start_time = 0 → start at file beginning
# - duration  = 0 → load until end-of-file

import hashlib
import math
import os
from numbers import Integral

import folder_paths  # type: ignore
import torch  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.audio import load_audio_file, save_audio_preview

# Kept as a private compatibility alias for callers that imported the old helper.
_load_trimmed = load_audio_file
_MISSING = object()


class RvAudio_LoadAudio(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        input_dir = folder_paths.get_input_directory()
        try:
            files = folder_paths.filter_files_content_types(
                os.listdir(input_dir), ["audio", "video"]
            )
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
                    default=0.0,
                    min=0.0,
                    max=86400.0,
                    step=0.01,
                    tooltip="Offset from the start of the audio source (seconds). 0 = beginning.",
                ),
                io.Float.Input(
                    "duration",
                    default=0.0,
                    min=0.0,
                    max=86400.0,
                    step=0.01,
                    tooltip="Maximum excerpt duration (seconds). 0 = load to end of source.",
                ),
                io.Audio.Input(
                    "audio_in",
                    optional=True,
                    lazy=True,
                    tooltip="Auto prefers incoming audio. Selected file ignores this connection and skips its upstream execution.",
                ),
                io.Boolean.Input(
                    "stop_review",
                    default=False,
                    socketless=True,
                    label_on="active",
                    label_off="bypass",
                    display_name="Stop (Result Review)",
                    tooltip="Preview the excerpt and stop before downstream execution. Queue unchanged again to continue; changed inputs require review again.",
                ),
                io.Combo.Input(
                    "source",
                    options=["Auto", "Selected file", "Incoming audio"],
                    default="Auto",
                    socketless=True,
                    display_name="Audio source",
                    tooltip="Switch the player immediately without queueing. Auto follows available incoming audio; Selected file ignores the cable; Incoming audio requires a usable input. New generated audio needs one execution before it can be previewed.",
                ),
            ],
            outputs=[
                io.Audio.Output("audio"),
                io.Float.Output("duration"),
            ],
            is_output_node=True,
        )

    @classmethod
    def check_lazy_status(cls, source="Auto", audio_in=_MISSING, **kwargs):
        return ["audio_in"] if source != "Selected file" and audio_in is None else []

    @classmethod
    def execute(
        cls,
        audio,
        start_time: float = 0.0,
        duration: float = 0.0,
        audio_in=None,
        stop_review=False,
        source="Auto",
    ) -> io.NodeOutput:
        if source not in ("Auto", "Selected file", "Incoming audio"):
            raise ValueError("Select Auto, Selected file, or Incoming audio.")
        if source == "Selected file":
            audio_in = None
        elif source == "Incoming audio" and audio_in is None:
            raise ValueError("Incoming audio is unavailable. Enable/connect its source or choose Selected file.")
        if not all(math.isfinite(v) and v >= 0 for v in (start_time, duration)):
            raise ValueError("start_time and duration must be finite, non-negative seconds.")
        if audio_in is not None:
            if not isinstance(audio_in, dict):
                raise ValueError("Malformed AUDIO: expected waveform and sample_rate.")
            waveform = audio_in.get("waveform")
            sample_rate = audio_in.get("sample_rate")
            if (
                not isinstance(waveform, torch.Tensor)
                or waveform.ndim != 3
                or not all(waveform.shape)
                or not waveform.is_floating_point()
            ):
                raise ValueError(
                    "Malformed AUDIO: waveform must be a non-empty floating tensor "
                    "[batch, channels, samples]."
                )
            if (
                isinstance(sample_rate, bool)
                or not isinstance(sample_rate, Integral)
                or sample_rate <= 0
            ):
                raise ValueError("Malformed AUDIO: sample_rate must be a positive integer.")
            if not torch.isfinite(waveform).all():
                raise ValueError("Malformed AUDIO: waveform contains non-finite samples.")
            start = round(start_time * sample_rate)
            end = waveform.shape[-1]
            if duration:
                end = min(end, start + round(duration * sample_rate))
            if start >= end:
                raise ValueError(
                    "Audio excerpt is empty: start is beyond the end or duration is too short."
                )
            out = dict(audio_in, waveform=waveform[..., start:end])
            preview = save_audio_preview(waveform, sample_rate)
            ui = {"audio_source": ["incoming"], "audio_preview": [preview]}
        else:
            audio_path = folder_paths.get_annotated_filepath(audio) if audio else ""
            if not os.path.isfile(audio_path):
                raise ValueError(f"Invalid fallback audio file: {audio}")
            waveform, sample_rate = _load_trimmed(
                audio_path, start_time=start_time, duration=duration
            )
            out = {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}
            if not waveform.shape[-1]:
                raise ValueError("Audio excerpt is empty.")
            ui = {"audio_source": ["file"]}
        loaded_duration = out["waveform"].shape[-1] / sample_rate
        if stop_review:
            import nodes  # type: ignore

            # ComfyUI publishes the returned UI before checking this flag at
            # the next node, matching Eclipse's existing review-stop nodes.
            nodes.interrupt_processing()
        return io.NodeOutput(out, loaded_duration, ui=ui)

    @classmethod
    def fingerprint_inputs(
        cls,
        audio,
        start_time: float = 0.0,
        duration: float = 0.0,
        audio_in=_MISSING,
        stop_review=False,
        source="Auto",
    ):
        # Link dependencies and widgets already participate in ComfyUI's cache key.
        # Hash the fallback file too: linked None values can select it at execution.
        audio_path = folder_paths.get_annotated_filepath(audio) if audio else ""
        m = hashlib.sha256()
        try:
            with open(audio_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    m.update(chunk)
        except Exception:
            pass
        return m.digest().hex()

    @classmethod
    def validate_inputs(cls, audio, audio_in=_MISSING, source="Auto"):
        # A supplied link is unresolved here; validate the fallback only when
        # execute receives its actual value. Keep normal socket type checking.
        if source == "Incoming audio":
            return True if audio_in is not _MISSING else "Incoming audio requires an audio_in connection."
        if source != "Selected file" and audio_in is not _MISSING:
            return True
        if not audio or not folder_paths.exists_annotated_filepath(audio):
            return f"Invalid audio file: {audio}"
        return True
