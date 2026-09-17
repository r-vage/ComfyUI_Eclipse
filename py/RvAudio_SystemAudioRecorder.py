# System Audio Recorder [Eclipse]

from __future__ import annotations

import comfy.model_management  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.system_audio import system_audio_recorder


class RvAudio_SystemAudioRecorder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="System Audio Recorder [Eclipse]",
            display_name="System Audio Recorder",
            category=CATEGORY.MAIN.value + CATEGORY.AUDIO.value,
            description=(
                "Records the selected system output at 48 kHz stereo. Use the node's "
                "Start button to begin capture and submit the workflow, then Stop to "
                "finalize the files and release downstream execution. Adjust leading-"
                "silence removal and use Update Output for more versions without "
                "recording again."
            ),
            not_idempotent=True,
            inputs=[
                io.String.Input(
                    "filename_prefix",
                    default="audio/system_recording",
                    tooltip=(
                        "Output-relative folder and filename prefix. Eclipse adds a "
                        "collision-safe counter and the requested extension."
                    ),
                ),
                io.Combo.Input(
                    "format",
                    options=["wav", "wav + mp3", "mp3"],
                    default="wav",
                    tooltip="Publish WAV, MP3, or both formats.",
                ),
                io.Combo.Input(
                    "mp3_bitrate",
                    options=["128 kbps", "192 kbps", "256 kbps", "320 kbps"],
                    default="320 kbps",
                    tooltip="Constant MP3 bitrate. Disabled when MP3 is not selected.",
                ),
                io.Combo.Input(
                    "output_device",
                    options=["Default"],
                    default="Default",
                    tooltip="System output device or monitor to capture.",
                ),
                io.Boolean.Input(
                    "strip_start_silence",
                    default=True,
                    label_on="enabled",
                    label_off="disabled",
                    tooltip=(
                        "Remove leading silence in 10 ms windows while preserving "
                        "50 ms before the detected onset."
                    ),
                ),
                io.Float.Input(
                    "silence_threshold_db",
                    default=-70.0,
                    min=-96.0,
                    max=-20.0,
                    step=1.0,
                    tooltip="Per-channel RMS activity threshold in dBFS.",
                ),
                io.String.Input(
                    "recording_token",
                    default="",
                    socketless=True,
                    tooltip="Private recorder session token managed by the Eclipse UI.",
                ),
            ],
            outputs=[
                io.Audio.Output("audio"),
                io.String.Output("wav_path"),
                io.String.Output("mp3_path"),
                io.Float.Output("duration"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls,
        filename_prefix: str,
        format: str,
        mp3_bitrate: str,
        output_device: str,
        strip_start_silence: bool,
        silence_threshold_db: float,
        recording_token: str,
    ) -> io.NodeOutput:
        del output_device
        if not recording_token:
            raise ValueError(
                "No active recording session. Press Start on the System Audio Recorder node."
            )
        recording = system_audio_recorder.wait_and_consume(
            node_id=str(cls.hidden.unique_id),
            token=recording_token,
            filename_prefix=filename_prefix,
            output_format=format,
            bitrate=mp3_bitrate,
            strip_start_silence=strip_start_silence,
            silence_threshold_db=silence_threshold_db,
            interrupt_check=(
                comfy.model_management.throw_exception_if_processing_interrupted
            ),
        )
        audio = {
            "waveform": recording.waveform.unsqueeze(0),
            "sample_rate": recording.sample_rate,
        }
        return io.NodeOutput(
            audio,
            recording.wav_path,
            recording.mp3_path,
            recording.duration,
        )
