# Trim to Shortest [Eclipse]
#
# Aligns the durations of input image batches (video frames) and audio waveforms.
# Crops the longer stream to match the duration of the shorter one at the target FPS,
# or aligns specifically based on the chosen trim_mode.
#
# Inputs:
# - images: [B, H, W, C] tensor representing video frames (optional)
# - audio: Dictionary containing waveform and sample_rate (optional)
# - fps: Target frame rate for duration translation
# - trim_mode: "shortest", "video_to_audio", or "audio_to_video"
#
# Outputs:
# - images: The aligned video frames
# - audio: The aligned audio dictionary

import math
import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import unwrap_value, flatten_images, was_input_batch, cat_and_fit_images, prepare_image_output

_LOG_PREFIX = "TrimToShortest"


def _audio_samples_for_video(num_frames: int, fps: float, sample_rate: int) -> int:
    return max(1, round(num_frames * sample_rate / fps))


class RvVideo_TrimToShortest(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Trim to Shortest [Eclipse]",
            display_name="Trim to Shortest",
            description=(
                "Aligns the durations of an image batch (video) and an audio track.\n"
                "Crops the longer stream to match the shorter one according to the trim_mode."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Image.Input(
                    "images", optional=True, tooltip="Input video frames (image batch)."
                ),
                io.Audio.Input("audio", optional=True, tooltip="Input audio track."),
                io.Float.Input(
                    "fps",
                    default=16.0,
                    min=1.0,
                    max=240.0,
                    step=0.01,
                    tooltip="Target frame rate (frames per second) of the video.",
                ),
                io.Combo.Input(
                    "trim_mode",
                    options=["shortest", "video_to_audio", "audio_to_video"],
                    default="shortest",
                    tooltip="How to align durations: shortest (trim both), video_to_audio (trim video), audio_to_video (trim audio).",
                ),
            ],
            outputs=[
                io.Image.Output("images", tooltip="Trimmed or original video frames."),
                io.Audio.Output("audio", tooltip="Trimmed or original audio track."),
            ],
        )

    @classmethod
    def execute(cls, fps, images=None, audio=None, trim_mode="shortest"):
        fps = unwrap_value(fps, 16.0)
        trim_mode = unwrap_value(trim_mode, "shortest")
        audio = unwrap_value(audio, None)

        if images is None and audio is None:
            return io.NodeOutput(None, None)

        if images is None:
            return io.NodeOutput(None, audio)

        flat_images = flatten_images(images)
        if not flat_images:
            return io.NodeOutput(None, audio)

        was_batch = was_input_batch(images)
        images_tensor = cat_and_fit_images(flat_images, log_prefix=_LOG_PREFIX)
        if images_tensor is None:
            return io.NodeOutput(None, audio)

        if audio is None:
            return io.NodeOutput(prepare_image_output(images_tensor, was_batch), None)

        try:
            num_frames = int(images_tensor.shape[0])
            wf = audio.get("waveform")
            sr = int(audio.get("sample_rate", 0))

            if wf is not None and sr > 0:
                if wf.ndim == 3:
                    wf_ch = wf[0]
                else:
                    wf_ch = wf
                audio_samples = int(wf_ch.shape[-1])

                # Calculate audio duration in video frames
                audio_frames = max(1, int(math.floor((audio_samples / sr) * fps)))

                target_video_frames = num_frames
                target_audio_samples = audio_samples

                if trim_mode == "video_to_audio":
                    target_video_frames = min(num_frames, audio_frames)
                elif trim_mode == "audio_to_video":
                    target_audio_samples = min(
                        audio_samples, _audio_samples_for_video(num_frames, fps, sr)
                    )
                elif trim_mode == "shortest":
                    target_video_frames = min(num_frames, audio_frames)
                    target_audio_samples = min(
                        audio_samples,
                        _audio_samples_for_video(target_video_frames, fps, sr),
                    )

                # Apply trims
                if target_video_frames < num_frames:
                    images_tensor = images_tensor[:target_video_frames]
                    log.msg(
                        _LOG_PREFIX,
                        f"Trimmed video: {num_frames} -> {target_video_frames} frames (fps={fps})",
                    )

                if target_audio_samples < audio_samples:
                    # Maintain dimensions: batch (if any) and channels
                    new_wf = (
                        wf[..., :target_audio_samples]
                        if wf.ndim == 3
                        else wf_ch[..., :target_audio_samples]
                    )
                    # Ensure it is at least 3D or formatted properly for ComfyUI
                    audio = {
                        "waveform": new_wf if new_wf.ndim == 3 else new_wf.unsqueeze(0),
                        "sample_rate": sr,
                    }
                    log.msg(
                        _LOG_PREFIX,
                        f"Trimmed audio: {audio_samples} -> {target_audio_samples} samples (sr={sr})",
                    )

        except Exception as e:
            log.error(_LOG_PREFIX, f"Alignment failed, passing inputs unmodified: {e}")

        return io.NodeOutput(prepare_image_output(images_tensor, was_batch), audio)
