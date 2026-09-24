# Standalone audio transcription, also usable as a lyric-caption source.
import json

from comfy_api.latest import io

from ..core import CATEGORY
from ..core.audio_transcription import TRANSCRIPTION_REVISION, transcribe_audio
from ..core.lyric_alignment import LANGUAGES, model_identity
from ..core.lyric_timing import shifted_lines, srt_text


class RvAudio_TranscribeAudio(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Transcribe Audio [Eclipse]",
            display_name="Transcribe Audio",
            category=CATEGORY.MAIN.value + CATEGORY.AUDIO.value,
            description="Recognize speech or sung words with the local Whisper large-v3 model. Outputs text and full-audio timing for review, subtitles or lyric captions. No supplied lyrics are required.",
            inputs=[
                io.Audio.Input("audio", tooltip="Complete recording. Output timestamps start at this audio's time zero."),
                io.Combo.Input("language", options=["Auto", *LANGUAGES], default="Auto"),
                io.Combo.Input("device", options=["auto", "cpu", "cuda"], default="auto"),
                io.Audio.Input("vocals", optional=True,
                               tooltip="Optional isolated vocals with the same time zero and duration as audio (within 50 ms)."),
            ],
            outputs=[io.String.Output("transcript"), io.String.Output("timing_json"),
                     io.String.Output("srt"), io.String.Output("report")],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return model_identity(), TRANSCRIPTION_REVISION

    @classmethod
    def execute(cls, audio, language="Auto", device="auto", vocals=None):
        text, data, report = transcribe_audio(audio, language, device, vocals)
        return io.NodeOutput(text, json.dumps(data, ensure_ascii=False, indent=2),
                             srt_text(shifted_lines(data, data["duration"])),
                             json.dumps(report, ensure_ascii=False, indent=2))
