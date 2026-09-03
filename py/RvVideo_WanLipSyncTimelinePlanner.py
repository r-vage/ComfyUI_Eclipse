# WAN LipSync Timeline Planner [Eclipse]
#
# Builds a metadata-only, frame-exact extension plan for ComfyUI's fixed-length
# WanInfiniteTalkToVideo workflow. Multi-reference plans can use manual timing
# targets or evenly spaced defaults, then optionally align those targets to
# activity gaps in the existing Wav2Vec2 encoder output.

import math
from typing import Any

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import flatten_images, unwrap_value
from ..core.logger import log

_LOG_PREFIX = "WAN LipSync Timeline Planner"
_ACTIVITY_FPS = 50.0
_PLAN_KIND = "WAN_LIPSYNC_PLAN"
_PLAN_VERSION = 1


def _audio_duration(audio: Any) -> float:
    if not isinstance(audio, dict):
        raise ValueError("audio must be a valid ComfyUI AUDIO value.")

    waveform = audio.get("waveform")
    sample_rate = audio.get("sample_rate")
    if not isinstance(waveform, torch.Tensor) or waveform.ndim < 1:
        raise ValueError("audio waveform is missing or invalid.")
    if not isinstance(sample_rate, (int, float)) or sample_rate <= 0:
        raise ValueError("audio sample rate must be greater than zero.")

    return waveform.shape[-1] / float(sample_rate)


def _parse_manual_transition_times(
    manual_transition_times: str,
    image_count: int,
    fps: float,
    total_frames: int,
    overlap_frames: int,
) -> tuple[list[float], list[int]] | None:
    value = manual_transition_times.strip()
    if not value:
        return None

    parts = [part.strip() for part in value.split(",")]
    if any(not part for part in parts):
        raise ValueError(
            "manual_transition_times must be comma-separated seconds without "
            "empty entries."
        )

    expected_count = image_count - 1
    if len(parts) != expected_count:
        raise ValueError(
            f"manual_transition_times requires exactly {expected_count} values "
            f"for {image_count} reference images; received {len(parts)}."
        )

    times: list[float] = []
    frames: list[int] = []
    for index, part in enumerate(parts):
        try:
            seconds = float(part)
        except ValueError as error:
            raise ValueError(
                f"Manual transition {index + 1} is not a valid number: {part!r}."
            ) from error
        if not math.isfinite(seconds):
            raise ValueError(
                f"Manual transition {index + 1} must be a finite number."
            )

        frame = round(seconds * fps)
        if frame < overlap_frames or frame >= total_frames:
            raise ValueError(
                f"Manual transition {index + 1} at {seconds:g}s resolves to frame "
                f"{frame}; it must be between frame {overlap_frames} and "
                f"{total_frames - 1}."
            )
        if frames and frame - frames[-1] < overlap_frames:
            raise ValueError(
                f"Manual transition {index + 1} at frame {frame} must be at least "
                f"{overlap_frames} frames after frame {frames[-1]}."
            )
        times.append(seconds)
        frames.append(frame)

    return times, frames


def _encoded_activity(audio_encoder_output: Any) -> tuple[torch.Tensor | None, str]:
    if not isinstance(audio_encoder_output, dict):
        return None, "encoder output is missing or is not a dictionary"

    layers = audio_encoder_output.get("encoded_audio_all_layers")
    if not isinstance(layers, (list, tuple)) or len(layers) < 2:
        return None, "encoded_audio_all_layers must contain at least two layers"

    normalized_layers: list[torch.Tensor] = []
    expected_shape: tuple[int, ...] | None = None
    for layer in layers:
        if not isinstance(layer, torch.Tensor):
            return None, "encoded audio layers must be tensors"
        if layer.ndim == 2:
            layer = layer.unsqueeze(0)
        if layer.ndim != 3 or layer.shape[1] < 2 or layer.shape[2] < 1:
            return None, "encoded audio layers must have shape [batch, time, features]"
        current_shape = tuple(layer.shape)
        if expected_shape is None:
            expected_shape = current_shape
        elif current_shape != expected_shape:
            return None, "encoded audio layers do not share one shape"
        normalized_layers.append(layer)

    # InfiniteTalk excludes the first hidden layer. Analyze the final third of
    # the same remaining layer stack so no additional audio model is needed.
    remaining_layers = normalized_layers[1:]
    selected_layers = remaining_layers[(2 * len(remaining_layers)) // 3 :]

    try:
        stacked = torch.stack(selected_layers, dim=0).float()
        cosine = F.cosine_similarity(
            stacked[:, :, 1:, :], stacked[:, :, :-1, :], dim=-1, eps=1e-8
        )
        changes = (1.0 - cosine).clamp_min(0.0).mean(dim=(0, 1))
        activity = torch.cat((changes[:1], changes), dim=0)
        activity = activity.detach().cpu()

        # Five native 50 Hz samples equal the requested 100 ms smoothing span.
        padded = F.pad(activity.view(1, 1, -1), (2, 2), mode="replicate")
        smoothed = F.avg_pool1d(padded, kernel_size=5, stride=1).view(-1)
    except (RuntimeError, TypeError, ValueError) as error:
        return None, f"failed to derive encoded activity: {error}"

    if smoothed.numel() < 2 or not torch.isfinite(smoothed).all().item():
        return None, "encoded activity is empty or contains non-finite values"
    return smoothed, ""


def _sustained_activity_start(
    high_mask: torch.Tensor, start_index: int, hold_samples: int
) -> int | None:
    last_start = high_mask.numel() - hold_samples
    for index in range(start_index, last_start + 1):
        if bool(high_mask[index : index + hold_samples].all().item()):
            return index
    return None


def _align_transition(
    activity: torch.Tensor,
    ideal_frame: int,
    fps: float,
    transition_edge: str,
    search_window_seconds: float,
    min_silence_duration: float,
    resume_hold_duration: float,
) -> tuple[int | None, str]:
    ideal_sample = ideal_frame / fps * _ACTIVITY_FPS
    search_samples = search_window_seconds * _ACTIVITY_FPS
    window_start = max(0, int(math.floor(ideal_sample - search_samples)))
    window_end = min(
        activity.numel(), int(math.ceil(ideal_sample + search_samples)) + 1
    )
    local_activity = activity[window_start:window_end]
    if local_activity.numel() < 2:
        return None, "activity search window is empty"

    silence_threshold = torch.quantile(local_activity, 0.30).item()
    resume_threshold = torch.quantile(local_activity, 0.60).item()
    low_mask = local_activity <= silence_threshold
    high_mask = local_activity >= resume_threshold
    silence_samples = max(1, math.ceil(min_silence_duration * _ACTIVITY_FPS))
    hold_samples = max(1, math.ceil(resume_hold_duration * _ACTIVITY_FPS))

    candidates: list[tuple[int, int, int, float]] = []
    index = 0
    while index < low_mask.numel():
        if not bool(low_mask[index].item()):
            index += 1
            continue

        run_start = index
        while index < low_mask.numel() and bool(low_mask[index].item()):
            index += 1
        run_end = index
        if run_end - run_start < silence_samples:
            continue

        resume_start = _sustained_activity_start(high_mask, run_end, hold_samples)
        if resume_start is None:
            continue

        global_start = window_start + run_start
        global_end = window_start + run_end
        global_resume = window_start + resume_start
        if ideal_sample < global_start:
            interval_distance = global_start - ideal_sample
        elif ideal_sample >= global_end:
            interval_distance = ideal_sample - (global_end - 1)
        else:
            interval_distance = 0.0
        candidates.append(
            (global_start, global_end, global_resume, interval_distance)
        )

    if not candidates:
        return (
            None,
            "no activity gap met both the silence and sustained-resume requirements",
        )

    def candidate_key(candidate: tuple[int, int, int, float]) -> tuple[float, float]:
        selected_sample = (
            candidate[0] if transition_edge == "silence_start" else candidate[2]
        )
        return candidate[3], abs(selected_sample - ideal_sample)

    selected = min(candidates, key=candidate_key)
    selected_sample = (
        selected[0] if transition_edge == "silence_start" else selected[2]
    )
    aligned_frame = round(selected_sample / _ACTIVITY_FPS * fps)
    details = (
        f"gap {selected[0] / _ACTIVITY_FPS:.3f}s-"
        f"{selected[1] / _ACTIVITY_FPS:.3f}s, resume "
        f"{selected[2] / _ACTIVITY_FPS:.3f}s, thresholds "
        f"{silence_threshold:.6f}/{resume_threshold:.6f}"
    )
    return aligned_frame, details


def _space_transitions(
    candidates: list[int], total_frames: int, overlap_frames: int
) -> tuple[list[int], list[str]]:
    transition_count = len(candidates)
    if transition_count == 0:
        return [], []

    minimum_last_frame = overlap_frames + (transition_count - 1) * overlap_frames
    if minimum_last_frame > total_frames - 1:
        raise ValueError(
            "Audio is too short for the requested image count and overlap spacing."
        )

    spaced: list[int] = []
    adjustments: list[str] = []
    for index, candidate in enumerate(candidates):
        lower_bound = overlap_frames if index == 0 else spaced[-1] + overlap_frames
        remaining = transition_count - index - 1
        upper_bound = total_frames - 1 - remaining * overlap_frames
        selected = min(max(candidate, lower_bound), upper_bound)
        if selected != candidate:
            adjustments.append(
                f"transition {index + 1} moved from frame {candidate} to {selected} "
                "to preserve ordering and overlap spacing"
            )
        spaced.append(selected)
    return spaced, adjustments


def _build_tasks(
    base_keep_frames: int,
    total_frames: int,
    transition_frames: list[int],
    context_length: int,
    overlap_frames: int,
) -> list[dict[str, int | bool]]:
    stride = context_length - overlap_frames
    transition_set = set(transition_frames)
    tasks: list[dict[str, int | bool]] = []
    start_frame = base_keep_frames

    while start_frame < total_frames:
        image_index = sum(
            1 for transition_frame in transition_frames if transition_frame <= start_frame
        )
        next_transition = (
            transition_frames[image_index]
            if image_index < len(transition_frames)
            else total_frames
        )
        end_frame = min(start_frame + stride, next_transition, total_frames)
        if end_frame <= start_frame:
            raise ValueError("Timeline planning produced an empty extension task.")
        tasks.append(
            {
                "start_frame": start_frame,
                "end_frame": end_frame,
                "image_index": image_index,
                "keep_frames": end_frame - start_frame,
                "starts_transition": start_frame in transition_set,
            }
        )
        start_frame = end_frame

    return tasks


class RvVideo_WanLipSyncTimelinePlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="WAN LipSync Timeline Planner [Eclipse]",
            display_name="WAN LipSync Timeline Planner",
            description=(
                "Builds a frame-exact fixed-length InfiniteTalk extension plan "
                "from manual clip-relative switch times or automatic spacing. "
                "Encoded Wav2Vec2 features are treated as activity, not as an "
                "explicit voice-activity classification."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Audio.Input(
                    "audio", tooltip="Audio that defines the final timeline length."
                ),
                io.AudioEncoderOutput.Input(
                    "audio_encoder_output",
                    optional=True,
                    tooltip="Optional existing Wav2Vec2 output already used by InfiniteTalk.",
                ),
                io.Image.Input("image_batch", tooltip="Ordered reference image batch."),
                io.Float.Input("fps", default=25.0, min=1.0, max=240.0, step=0.01),
                io.Int.Input("context_length", default=81, min=2, max=4096, step=1),
                io.Int.Input("overlap_frames", default=9, min=1, max=4095, step=1),
                io.String.Input(
                    "manual_transition_times",
                    default="",
                    tooltip=(
                        "Comma-separated image-switch times in seconds, one for "
                        "each image after the first. Leave blank for evenly spaced "
                        "automatic targets. Disable silence alignment to preserve "
                        "the exact requested frames."
                    ),
                ),
                io.Boolean.Input(
                    "align_to_silence",
                    default=True,
                    tooltip=(
                        "When enabled, search near each manual or automatic target "
                        "for an encoded-audio activity gap. Disable for exact target "
                        "frames."
                    ),
                ),
                io.Combo.Input(
                    "transition_edge",
                    options=["activity_resume", "silence_start"],
                    default="activity_resume",
                ),
                io.Float.Input(
                    "search_window_seconds",
                    default=1.5,
                    min=0.0,
                    max=30.0,
                    step=0.05,
                ),
                io.Float.Input(
                    "min_silence_duration",
                    default=0.5,
                    min=0.02,
                    max=30.0,
                    step=0.02,
                ),
                io.Float.Input(
                    "resume_hold_duration",
                    default=0.2,
                    min=0.02,
                    max=30.0,
                    step=0.02,
                ),
            ],
            outputs=[
                io.Custom(_PLAN_KIND).Output("plan"),
                io.Int.Output("task_count"),
                io.Int.Output("total_frames"),
                io.Int.Output("base_keep_frames"),
                io.String.Output("report"),
            ],
        )

    @classmethod
    def execute(
        cls,
        audio,
        image_batch,
        fps,
        context_length,
        overlap_frames,
        manual_transition_times,
        align_to_silence,
        transition_edge,
        search_window_seconds,
        min_silence_duration,
        resume_hold_duration,
        audio_encoder_output=None,
    ):
        fps = unwrap_value(fps, 25.0)
        context_length = unwrap_value(context_length, 81)
        overlap_frames = unwrap_value(overlap_frames, 9)
        manual_transition_times = unwrap_value(manual_transition_times, "")
        align_to_silence = unwrap_value(align_to_silence, True)
        transition_edge = unwrap_value(transition_edge, "activity_resume")
        search_window_seconds = unwrap_value(search_window_seconds, 1.5)
        min_silence_duration = unwrap_value(min_silence_duration, 0.5)
        resume_hold_duration = unwrap_value(resume_hold_duration, 0.2)

        if fps <= 0:
            raise ValueError("fps must be greater than zero.")
        if context_length <= 1:
            raise ValueError("context_length must be greater than one frame.")
        if overlap_frames < 1 or overlap_frames >= context_length:
            raise ValueError(
                "overlap_frames must be at least one and smaller than context_length."
            )
        if not isinstance(manual_transition_times, str):
            raise ValueError("manual_transition_times must be a string.")
        if search_window_seconds < 0:
            raise ValueError("search_window_seconds cannot be negative.")
        if min_silence_duration <= 0 or resume_hold_duration <= 0:
            raise ValueError("Silence and resume durations must be greater than zero.")
        if transition_edge not in ("activity_resume", "silence_start"):
            raise ValueError(f"Unsupported transition_edge: {transition_edge}")

        images = flatten_images(image_batch)
        image_count = len(images)
        if image_count == 0:
            raise ValueError("image_batch must contain at least one image.")

        duration = _audio_duration(audio)
        total_frames = math.ceil(duration * fps)
        if total_frames < 1:
            raise ValueError("Audio must contain at least one output video frame.")

        report_lines = [
            f"Timeline: {total_frames} frames at {fps:g} fps ({duration:.3f}s)",
            f"Fixed render length: {context_length}; retained stride: "
            f"{context_length - overlap_frames}",
            f"Reference images: {image_count}",
        ]

        if image_count == 1:
            mode = "single_reference"
            transition_source = "single_reference"
            manual_times: list[float] = []
            transition_frames: list[int] = []
            base_keep_frames = min(context_length, total_frames)
            tasks = _build_tasks(
                base_keep_frames,
                total_frames,
                transition_frames,
                context_length,
                overlap_frames,
            )
            report_lines.append(
                "Single reference: encoded-audio transition analysis was skipped."
            )
            if manual_transition_times.strip():
                report_lines.append(
                    "Single reference: manual transition times were ignored."
                )
        else:
            mode = "multi_reference"
            manual_data = _parse_manual_transition_times(
                manual_transition_times,
                image_count,
                fps,
                total_frames,
                overlap_frames,
            )
            if manual_data is None:
                transition_source = "evenly_spaced"
                manual_times = []
                target_frames = [
                    round(total_frames * index / image_count)
                    for index in range(1, image_count)
                ]
                report_lines.append(f"Automatic transition frames: {target_frames}")
            else:
                transition_source = "manual_seconds"
                manual_times, target_frames = manual_data
                report_lines.append(f"Manual transition seconds: {manual_times}")
                report_lines.append(f"Manual transition frames: {target_frames}")

            candidates = list(target_frames)

            if not align_to_silence:
                report_lines.append(
                    "Encoded-audio alignment disabled; target transition frames retained."
                )
            else:
                activity, activity_error = _encoded_activity(audio_encoder_output)
                if activity is None:
                    report_lines.append(
                        "Encoded-audio alignment fallback: "
                        f"{activity_error}; target transition frames retained."
                    )
                else:
                    report_lines.append(
                        "Encoded-audio activity alignment uses the final third of "
                        "InfiniteTalk's post-first-layer Wav2Vec2 features."
                    )
                    for index, target_frame in enumerate(target_frames):
                        aligned_frame, details = _align_transition(
                            activity,
                            target_frame,
                            fps,
                            transition_edge,
                            search_window_seconds,
                            min_silence_duration,
                            resume_hold_duration,
                        )
                        if aligned_frame is None:
                            report_lines.append(
                                f"Transition {index + 1}: frame {target_frame} "
                                f"fallback; {details}."
                            )
                        else:
                            candidates[index] = aligned_frame
                            report_lines.append(
                                f"Transition {index + 1}: frame {target_frame} -> "
                                f"{aligned_frame} ({transition_edge}); {details}."
                            )

            transition_frames, adjustments = _space_transitions(
                candidates, total_frames, overlap_frames
            )
            report_lines.extend(f"Spacing adjustment: {item}." for item in adjustments)
            base_keep_frames = min(
                context_length, total_frames, transition_frames[0]
            )
            tasks = _build_tasks(
                base_keep_frames,
                total_frames,
                transition_frames,
                context_length,
                overlap_frames,
            )

        if tasks and tasks[-1]["keep_frames"] == 1:
            omitted_task = tasks.pop()
            original_total_frames = total_frames
            total_frames = int(omitted_task["start_frame"])
            transition_frames = [
                frame for frame in transition_frames if frame < total_frames
            ]
            report_lines.append(
                "Terminal one-frame task skipped: "
                f"[{omitted_task['start_frame']},{omitted_task['end_frame']})/image "
                f"{omitted_task['image_index']} was dropped because it would retain "
                "only one frame; output is one frame shorter "
                f"({original_total_frames} -> {total_frames} frames)."
            )

        plan: dict[str, Any] = {
            "kind": _PLAN_KIND,
            "version": _PLAN_VERSION,
            "mode": mode,
            "transition_source": transition_source,
            "manual_transition_times": manual_times,
            "fps": fps,
            "context_length": context_length,
            "overlap_frames": overlap_frames,
            "transition_edge": transition_edge,
            "align_to_silence": align_to_silence,
            "search_window_seconds": search_window_seconds,
            "min_silence_duration": min_silence_duration,
            "resume_hold_duration": resume_hold_duration,
            "image_count": image_count,
            "total_frames": total_frames,
            "transition_frames": transition_frames,
            "base_keep_frames": base_keep_frames,
            "tasks": tasks,
        }
        report_lines.append(f"Base crop: {base_keep_frames} frames")
        report_lines.append(f"Extension tasks: {len(tasks)}")
        report_lines.append(
            "Task ranges: "
            + ", ".join(
                f"[{task['start_frame']},{task['end_frame']})/image {task['image_index']}"
                for task in tasks
            )
        )
        report = "\n".join(report_lines)
        log.debug(_LOG_PREFIX, report)
        return io.NodeOutput(
            plan, len(tasks), total_frames, base_keep_frames, report
        )
