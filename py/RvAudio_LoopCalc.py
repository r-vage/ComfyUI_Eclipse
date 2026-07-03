# Loop Calculator (Audio) [Eclipse]
#
# Computes the loop_count needed to generate enough frames to cover an audio
# clip's duration, given a target frame rate and the Smart Folder context
# length. Mirrors Smart Folder's frame budgeting: frame_load_cap = context_length * loop_count.
#
#   total_frames_needed = ceil(duration * fps)
#   loop_count          = ceil(total_frames_needed / context_length)
#
# AUDIO input is optional — when wired, its duration overrides the manual
# `duration` widget (waveform.shape[-1] / sample_rate).

import math

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Loop Calc Audio"


class RvAudio_LoopCalc(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Loop Calculator Audio [Eclipse]",
            display_name="Loop Calculator (Audio)",
            category=CATEGORY.MAIN.value + CATEGORY.AUDIO.value,
            inputs=[
                io.Float.Input(
                    "duration", default=0.0, min=0.0, max=86400.0, step=0.01,
                    tooltip="Audio duration in seconds. Ignored when an AUDIO input is connected.",
                ),
                io.Float.Input(
                    "fps", default=16.0, min=1.0, max=240.0, step=0.01,
                    tooltip="Target frame rate (frames per second).",
                ),
                io.Int.Input(
                    "context_length", default=81, min=1, max=4096, step=1,
                    tooltip="Smart Folder context length.",
                ),
                io.Int.Input(
                    "overlap_frames", default=9, min=0, max=4096, step=1,
                    tooltip="Number of overlapping frames between loops (e.g. motion_frame_count in InfiniteTalkToVideo).",
                ),
                io.Audio.Input(
                    "audio", optional=True,
                    tooltip="Optional AUDIO input. When connected, its duration overrides the duration widget.",
                ),
            ],
            outputs=[
                io.Int.Output("loop_count", tooltip="Number of additional loops (iterations) needed for extend sampling feedback loop (excluding the base loop)."),
                io.Int.Output("total_frames"),
                io.Float.Output("duration"),
            ],
        )

    @classmethod
    def execute(cls, duration, fps, context_length, overlap_frames, audio=None):
        try:
            d = duration if duration is not None else 0.0
            if audio is not None:
                try:
                    wf = audio.get("waveform")
                    sr = int(audio.get("sample_rate", 0))
                    if wf is not None and sr > 0:
                        d = wf.shape[-1] / sr
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"Failed to read AUDIO duration: {e}")

            f = max(1.0, fps)
            cl = max(1, context_length)
            ol = max(0, overlap_frames)
            total_frames = math.ceil(d * f)
            
            effective_stride = max(1, cl - ol)
            if total_frames <= cl:
                loop_count = 0
            else:
                loop_count = math.ceil((total_frames - cl) / effective_stride)
            loop_count = max(0, loop_count)
            
            return io.NodeOutput(loop_count, total_frames, d)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Calculation failed: {e}")
            return io.NodeOutput(0, 0, 0.0)
