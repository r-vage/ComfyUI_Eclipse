# WAN LipSync Timeline Planner [Eclipse]
#
# Builds a metadata-only, frame-exact extension plan for ComfyUI's fixed-length
# WanInfiniteTalkToVideo workflow. Multi-reference plans can use manual timing
# targets or evenly spaced defaults, then optionally align those targets to
# activity gaps in the existing Wav2Vec2 encoder output.

import math
from typing import Any

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.audio_timeline import (
    align_activity_gap,
    audio_duration_seconds,
    encoded_audio_activity,
)
from ..core.image_helpers import flatten_images, unwrap_value
from ..core.logger import log

_LOG_PREFIX = "WAN LipSync Timeline Planner"
_PLAN_KIND = "WAN_LIPSYNC_PLAN"
_PLAN_VERSION = 1


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

        duration = audio_duration_seconds(audio)
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
                activity, activity_error = encoded_audio_activity(
                    audio_encoder_output
                )
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
                        aligned_frame, details = align_activity_gap(
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
