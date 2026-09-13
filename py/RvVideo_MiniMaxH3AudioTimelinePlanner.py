# MiniMax H3 Audio Timeline Planner [Eclipse]
#
# Builds an image-aware, frame- and sample-exact external-audio plan for H3's
# native 24 FPS timeline and 17k+5 render grid.

import math
from itertools import pairwise
from typing import Any

import torch  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.audio_timeline import (
    align_activity_gap,
    audio_duration_seconds,
    encoded_audio_activity,
)
from ..core.image_helpers import flatten_images, unwrap_value
from ..core.logger import log

_LOG_PREFIX = "MiniMax H3 Audio Timeline Planner"
_PLAN_KIND = "MINIMAX_H3_AUDIO_PLAN"
_PLAN_VERSION = 4
_H3_FPS = 24.0
_MIN_RENDER_FRAMES = 124
_MAX_RENDER_FRAMES = 362
_OVERLAP_FRAMES = 22
_DESTINATION_CROP_FRAMES = 23
_DESTINATION_ANCHOR_FRAME = 22


def _is_h3_frame_count(frame_count: int) -> bool:
    return frame_count >= 5 and frame_count % 17 == 5


def _aligned_h3_length(required_frames: int) -> int:
    required_frames = max(_MIN_RENDER_FRAMES, required_frames)
    return required_frames + (5 - required_frames) % 17


def _balanced_task_ranges(
    output_start: int,
    output_end: int,
    first_crop_frames: int,
    lookahead_frames: int,
    max_render_frames: int,
) -> list[tuple[int, int, int, int]]:
    """Split a retained span into the fewest balanced legal H3 renders."""
    retained_frames = output_end - output_start
    if retained_frames < 1:
        raise ValueError("A planned retained span must contain at least one frame.")

    one_task_capacity = max_render_frames - first_crop_frames - lookahead_frames
    if (
        retained_frames > one_task_capacity
        and lookahead_frames >= max_render_frames - _OVERLAP_FRAMES
    ):
        raise ValueError(
            "The cropped lookahead does not fit inside max_render_frames after "
            "the required 22-frame continuity clip. Increase max_render_frames "
            "or shorten bridge_window_seconds."
        )

    task_count = 1
    while True:
        crops = [first_crop_frames, *([_OVERLAP_FRAMES] * (task_count - 1))]
        retained_capacity = sum(max_render_frames - crop for crop in crops)
        retained_capacity -= lookahead_frames
        if retained_frames <= retained_capacity:
            break
        task_count += 1

    crops = [first_crop_frames, *([_OVERLAP_FRAMES] * (task_count - 1))]
    total_required = retained_frames + sum(crops) + lookahead_frames
    remaining_keep = retained_frames
    ranges: list[tuple[int, int, int, int]] = []
    current_start = output_start
    for index, crop_start in enumerate(crops):
        remaining_tasks = task_count - index
        tail = lookahead_frames if index == task_count - 1 else 0
        later_capacity = sum(
            max_render_frames - crop for crop in crops[index + 1 :]
        ) - lookahead_frames
        minimum_keep = max(1, remaining_keep - later_capacity)
        maximum_keep = min(
            max_render_frames - crop_start - tail,
            remaining_keep - (remaining_tasks - 1),
        )
        target_required = round(total_required / task_count)
        desired_keep = target_required - crop_start - tail
        keep_frames = max(minimum_keep, min(desired_keep, maximum_keep))
        if index == task_count - 1:
            keep_frames = remaining_keep

        required_render = crop_start + keep_frames + tail
        render_frames = _aligned_h3_length(required_render)
        if render_frames > max_render_frames:
            raise ValueError("A balanced task exceeds max_render_frames.")
        current_end = current_start + keep_frames
        ranges.append((current_start, current_end, crop_start, render_frames))
        current_start = current_end
        remaining_keep -= keep_frames

    if current_start != output_end or remaining_keep != 0:
        raise RuntimeError("Balanced H3 task splitting did not consume its span.")
    return ranges


def _audio_shape(audio: Any) -> tuple[torch.Tensor, int]:
    audio_duration_seconds(audio)
    waveform = audio["waveform"]
    sample_rate = audio["sample_rate"]
    if (not isinstance(sample_rate, int) or isinstance(sample_rate, bool)) and (
        not isinstance(sample_rate, float) or not sample_rate.is_integer()
    ):
        raise ValueError("audio sample rate must be an integer value.")
    return waveform, int(sample_rate)


def _frame_to_sample(frame: int, sample_rate: int) -> int:
    return round(frame / _H3_FPS * sample_rate)


def _parse_manual_transition_times(
    value: str,
    image_count: int,
    total_frames: int,
) -> tuple[list[float], list[int]] | None:
    stripped = value.strip()
    if not stripped:
        return None

    parts = [part.strip() for part in stripped.split(",")]
    if any(not part for part in parts):
        raise ValueError("manual_transition_times contains an empty value.")
    if len(parts) != image_count - 1:
        raise ValueError(
            "manual_transition_times must contain exactly one time for every "
            "image after the first."
        )

    try:
        times = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(
            "manual_transition_times must contain comma-separated seconds."
        ) from error

    if any(not math.isfinite(value) for value in times):
        raise ValueError("manual_transition_times must contain finite values.")
    if any(value <= 0 for value in times):
        raise ValueError("manual transition times must be greater than zero.")
    if any(current <= previous for previous, current in pairwise(times)):
        raise ValueError("manual transition times must be strictly increasing.")

    frames = [round(value * _H3_FPS) for value in times]
    if any(frame >= total_frames for frame in frames):
        raise ValueError("manual transition times must be inside the audio timeline.")
    if len(set(frames)) != len(frames):
        raise ValueError("manual transition times resolve to duplicate video frames.")
    return times, frames


def _space_transition_frames(
    candidates: list[int], total_frames: int
) -> tuple[list[int], list[str]]:
    if not candidates:
        return [], []
    if total_frames <= len(candidates):
        raise ValueError(
            "The audio is too short to retain at least one frame for every image."
        )

    adjusted: list[int] = []
    notes: list[str] = []
    for index, candidate in enumerate(candidates):
        minimum = 1 if index == 0 else adjusted[-1] + 1
        maximum = total_frames - (len(candidates) - index)
        if minimum > maximum:
            raise ValueError(
                "The audio is too short to retain every image transition."
            )
        resolved = max(minimum, min(candidate, maximum))
        if resolved != candidate:
            notes.append(f"transition {index + 1}: frame {candidate} -> {resolved}")
        adjusted.append(resolved)
    return adjusted, notes


def _single_image_gap_boundaries(
    activity: torch.Tensor,
    total_frames: int,
    transition_edge: str,
    min_gap_duration: float,
    resume_hold_duration: float,
    max_render_frames: int,
) -> tuple[list[int], list[str]]:
    """Place same-image continuation seams at eligible sentence gaps."""
    boundaries: list[int] = []
    notes: list[str] = []
    output_start = 0

    while True:
        crop_start = 0 if not boundaries else _OVERLAP_FRAMES
        minimum_keep = _MIN_RENDER_FRAMES - crop_start
        maximum_keep = max_render_frames - crop_start
        minimum_end = output_start + minimum_keep
        if minimum_end >= total_frames:
            break

        # Leave enough frames for another valid retained task. If the current
        # span already fits, a gap is optional; otherwise the maximum H3 span is
        # a mandatory fallback boundary.
        maximum_end = min(
            output_start + maximum_keep,
            total_frames - (_MIN_RENDER_FRAMES - _OVERLAP_FRAMES),
        )
        if maximum_end < minimum_end:
            break

        scan_seconds = (maximum_end - minimum_end) / _H3_FPS
        aligned_frame, details = align_activity_gap(
            activity,
            minimum_end,
            _H3_FPS,
            transition_edge,
            scan_seconds,
            min_gap_duration,
            resume_hold_duration,
            minimum_frame=minimum_end,
            maximum_frame=maximum_end,
        )
        if aligned_frame is None:
            if total_frames - output_start <= maximum_keep:
                break
            aligned_frame = output_start + maximum_keep
            notes.append(
                f"continuation {len(boundaries) + 1}: frame {aligned_frame} "
                f"(maximum H3 span); {details}."
            )
        else:
            notes.append(
                f"continuation {len(boundaries) + 1}: frame {aligned_frame} "
                f"({transition_edge}); {details}."
            )

        boundaries.append(aligned_frame)
        output_start = aligned_frame

    return boundaries, notes


def _build_plan_tasks(
    transition_frames: list[int],
    continuation_frames: list[int],
    total_frames: int,
    sample_count: int,
    sample_rate: int,
    conditioning_sample_count: int,
    conditioning_sample_rate: int,
    image_count: int,
    transition_mode: str,
    bridge_span: str,
    bridge_window_frames: int,
    cropped_lookahead_conditioning: str,
    max_render_frames: int,
) -> list[dict[str, int | bool | None]]:
    tasks: list[dict[str, int | bool | None]] = []
    regions: list[
        tuple[
            int,
            int,
            int,
            int | None,
            int | None,
            int | None,
            int | None,
            int | None,
            int | None,
            bool,
            bool,
        ]
    ] = []
    hard_switch = transition_mode == "boundary_switch" or (
        bridge_span == "short_window" and bridge_window_frames == 0
    )

    if image_count == 1:
        starts = [0, *continuation_frames]
        ends = [*continuation_frames, total_frames]
        regions = [
            (start, end, 0, None, None, None, None, None, None, False, False)
            for start, end in zip(starts, ends)
        ]
    elif hard_switch:
        starts = [0, *transition_frames]
        ends = [*transition_frames, total_frames]
        for image_index, (start, end) in enumerate(zip(starts, ends)):
            visible_frame = start if image_index else None
            regions.append(
                (
                    start,
                    end,
                    image_index,
                    visible_frame,
                    image_index if image_index else None,
                    None,
                    None,
                    None,
                    None,
                    image_index > 0,
                    False,
                )
            )
    elif bridge_span == "full_interval":
        start = 0
        for image_index, transition_frame in enumerate(transition_frames):
            end = transition_frame + 1
            regions.append(
                (
                    start,
                    end,
                    image_index,
                    transition_frame,
                    image_index + 1,
                    None,
                    None,
                    None,
                    None,
                    False,
                    False,
                )
            )
            start = end
        if start < total_frames:
            regions.append(
                (
                    start,
                    total_frames,
                    image_count - 1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    False,
                    False,
                )
            )
    else:
        introductions: list[int] = []
        for index, transition_frame in enumerate(transition_frames):
            minimum = 0 if index == 0 else transition_frames[index - 1] + 1
            introduction = max(minimum, transition_frame - bridge_window_frames)
            if index == 0 and introduction < _OVERLAP_FRAMES:
                introduction = 0
            if introductions and introduction <= introductions[-1]:
                raise ValueError(
                    "Transition windows overlap too tightly to assign one visible "
                    "image introduction per H3 task region."
                )
            introductions.append(introduction)

        current_start = 0
        for transition_index, (introduction, transition_frame) in enumerate(
            zip(introductions, transition_frames)
        ):
            current_image = transition_index
            destination_image = transition_index + 1
            visible_frame = current_start if transition_index else None
            visible_image = current_image if transition_index else None

            if current_start < introduction:
                # Preserve the existing optional cropped lookahead on the block
                # immediately before bridge motion begins.
                regions.append(
                    (
                        current_start,
                        introduction,
                        current_image,
                        visible_frame,
                        visible_image,
                        None,
                        None,
                        transition_frame,
                        destination_image,
                        False,
                        visible_frame is not None,
                    )
                )
                visible_frame = None
                visible_image = None

            # Retain bridge motion only through the frame immediately before the
            # requested transition. Inject the destination at the transition as
            # the bridge's first discarded frame.
            regions.append(
                (
                    introduction,
                    transition_frame,
                    current_image,
                    visible_frame,
                    visible_image,
                    transition_frame,
                    destination_image,
                    None,
                    None,
                    False,
                    False,
                )
            )
            current_start = transition_frame

        regions.append(
            (
                current_start,
                total_frames,
                image_count - 1,
                current_start,
                image_count - 1,
                None,
                None,
                None,
                None,
                False,
                True,
            )
        )

    for (
        region_start,
        region_end,
        region_prompt_image,
        visible_output_frame,
        visible_image_index,
        bridge_endpoint_output_frame,
        bridge_endpoint_image_index,
        future_output_frame,
        future_image_index,
        hard_reset,
        destination_handoff,
    ) in regions:
        if region_end <= region_start:
            continue
        sections = [(region_start, region_end)]

        for section_index, (section_start, section_end) in enumerate(sections):
            first_range_is_destination = destination_handoff and section_index == 0
            if first_range_is_destination:
                first_crop = _DESTINATION_CROP_FRAMES
            elif not tasks or (hard_reset and section_index == 0):
                first_crop = 0
            else:
                first_crop = _OVERLAP_FRAMES
            is_last_section = section_index == len(sections) - 1
            lookahead_frames = (
                future_output_frame - section_end + 1
                if is_last_section and future_output_frame is not None
                else 0
            )
            if is_last_section and bridge_endpoint_output_frame is not None:
                lookahead_frames = max(
                    lookahead_frames,
                    bridge_endpoint_output_frame - section_end + 1,
                )
            if lookahead_frames < 0:
                raise ValueError(
                    "A cropped lookahead anchor must follow its retained span."
                )
            ranges = _balanced_task_ranges(
                section_start,
                section_end,
                first_crop,
                lookahead_frames,
                max_render_frames,
            )
            for range_index, (
                output_start,
                output_end,
                crop_start,
                render_frames,
            ) in enumerate(ranges):
                is_last_range = (
                    is_last_section and range_index == len(ranges) - 1
                )
                tasks.append(
                    _build_task(
                        task_index=len(tasks),
                        output_start=output_start,
                        output_end=output_end,
                        crop_start=crop_start,
                        render_frames=render_frames,
                        region_prompt_image=region_prompt_image,
                        visible_output_frame=visible_output_frame,
                        visible_image_index=visible_image_index,
                        bridge_endpoint_output_frame=bridge_endpoint_output_frame,
                        bridge_endpoint_image_index=bridge_endpoint_image_index,
                        future_output_frame=future_output_frame,
                        future_image_index=future_image_index,
                        is_last_range=is_last_range,
                        starts_hard_cut=(
                            hard_reset
                            and section_index == 0
                            and range_index == 0
                        ),
                        is_destination_handoff=(
                            first_range_is_destination and range_index == 0
                        ),
                        cropped_lookahead_conditioning=(
                            cropped_lookahead_conditioning
                        ),
                        total_frames=total_frames,
                        sample_count=sample_count,
                        sample_rate=sample_rate,
                        conditioning_sample_count=conditioning_sample_count,
                        conditioning_sample_rate=conditioning_sample_rate,
                    )
                )

    if (
        len(tasks) > 1
        and tasks[1]["output_start_frame"] < _OVERLAP_FRAMES
        and tasks[1]["has_continuity"]
    ):
        raise ValueError(
            "The first planned task must retain at least 22 frames for H3 continuity."
        )
    if image_count == 1 and any(task["prompt_image_index"] != 0 for task in tasks):
        raise ValueError("Single-image planning produced invalid image metadata.")
    return tasks


def _build_task(
    *,
    task_index: int,
    output_start: int,
    output_end: int,
    crop_start: int,
    render_frames: int,
    region_prompt_image: int,
    visible_output_frame: int | None,
    visible_image_index: int | None,
    bridge_endpoint_output_frame: int | None,
    bridge_endpoint_image_index: int | None,
    future_output_frame: int | None,
    future_image_index: int | None,
    is_last_range: bool,
    starts_hard_cut: bool,
    is_destination_handoff: bool,
    cropped_lookahead_conditioning: str,
    total_frames: int,
    sample_count: int,
    sample_rate: int,
    conditioning_sample_count: int,
    conditioning_sample_rate: int,
) -> dict[str, int | bool | None]:
    render_start = output_start - crop_start
    keep_frames = output_end - output_start

    prompt_image_index = region_prompt_image
    if visible_output_frame is not None and output_start > visible_output_frame:
        prompt_image_index = int(visible_image_index)

    task_visible_output = None
    task_visible_image = None
    if (
        visible_output_frame is not None
        and output_start <= visible_output_frame < output_end
    ):
        task_visible_output = visible_output_frame
        task_visible_image = visible_image_index
    task_visible_frame = None
    if task_visible_output is not None:
        task_visible_frame = (
            _DESTINATION_ANCHOR_FRAME
            if is_destination_handoff
            else task_visible_output - render_start
        )

    task_bridge_endpoint_output = (
        bridge_endpoint_output_frame if is_last_range else None
    )
    task_bridge_endpoint_image = (
        bridge_endpoint_image_index if is_last_range else None
    )
    task_bridge_endpoint_frame = (
        None
        if task_bridge_endpoint_output is None
        else task_bridge_endpoint_output - render_start
    )

    task_lookahead_output = future_output_frame if is_last_range else None
    task_lookahead_image = (
        future_image_index
        if is_last_range and cropped_lookahead_conditioning == "future_image"
        else None
    )
    task_lookahead_frame = (
        None
        if task_lookahead_image is None
        else task_lookahead_output - render_start
    )

    requested_audio_start = _frame_to_sample(
        render_start, conditioning_sample_rate
    )
    requested_audio_end = _frame_to_sample(
        render_start + render_frames, conditioning_sample_rate
    )
    audio_start_sample = max(
        0, min(conditioning_sample_count, requested_audio_start)
    )
    audio_end_sample = max(
        0, min(conditioning_sample_count, requested_audio_end)
    )
    return {
        "task_index": task_index,
        "prompt_image_index": prompt_image_index,
        "render_start_frame": render_start,
        "render_frames": render_frames,
        "output_start_frame": output_start,
        "output_end_frame": output_end,
        "crop_start_frames": crop_start,
        "keep_frames": keep_frames,
        "trailing_crop_frames": render_frames - crop_start - keep_frames,
        "audio_start_sample": audio_start_sample,
        "audio_end_sample": audio_end_sample,
        "audio_pad_start_samples": max(0, -requested_audio_start),
        "audio_pad_end_samples": max(
            0, requested_audio_end - conditioning_sample_count
        ),
        "output_start_sample": min(
            sample_count, _frame_to_sample(output_start, sample_rate)
        ),
        "output_end_sample": min(
            sample_count, _frame_to_sample(output_end, sample_rate)
        ),
        "visible_transition_image_index": task_visible_image,
        "visible_transition_frame_index": task_visible_frame,
        "visible_transition_output_frame": task_visible_output,
        "bridge_endpoint_image_index": task_bridge_endpoint_image,
        "bridge_endpoint_frame_index": task_bridge_endpoint_frame,
        "bridge_endpoint_output_frame": task_bridge_endpoint_output,
        "lookahead_image_index": task_lookahead_image,
        "lookahead_frame_index": task_lookahead_frame,
        "lookahead_output_frame": (
            task_lookahead_output if task_lookahead_image is not None else None
        ),
        "has_continuity": crop_start == _OVERLAP_FRAMES,
        "is_destination_handoff": is_destination_handoff,
        "starts_hard_cut": starts_hard_cut,
        "is_final": output_end == total_frames,
    }


class RvVideo_MiniMaxH3AudioTimelinePlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMax H3 Audio Timeline Planner [Eclipse]",
            display_name="MiniMax H3 Audio Timeline Planner",
            description=(
                "Plans exact external-audio H3 tasks for one or more images. "
                "Render lengths adapt to the 17k+5 grid from 124 through 362 "
                "frames. Transition references remain hidden while their first "
                "visible timestamp frames are generated."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Audio.Input(
                    "audio",
                    tooltip=(
                        "Master audio that sets the exact timeline duration. The "
                        "workflow keeps this audio for the final mux and discards "
                        "audio generated by H3."
                    ),
                ),
                io.Image.Input(
                    "image_batch",
                    tooltip=(
                        "Reference images in timeline order. Image 1 starts at "
                        "frame 0. Each later image owns output beginning at its "
                        "planned transition, even when its exact anchor is cropped."
                    ),
                ),
                io.AudioEncoderOutput.Input(
                    "audio_encoder_output",
                    optional=True,
                    tooltip=(
                        "Optional Wav2Vec features from AudioEncoderEncode. The "
                        "included workflow analyzes raw Demucs vocals. These "
                        "features locate activity gaps and never replace either "
                        "audio input."
                    ),
                ),
                io.String.Input(
                    "manual_transition_times",
                    default="",
                    multiline=True,
                    tooltip=(
                        "One comma-separated time in seconds for every image after "
                        "the first, for example 7, 13.5, 20. Leave blank for evenly "
                        "spaced targets. Activity alignment may move those targets."
                    ),
                ),
                io.Combo.Input(
                    "transition_mode",
                    options=["first_last_bridge", "boundary_switch"],
                    default="first_last_bridge",
                    tooltip=(
                        "first_last_bridge creates guided motion into the next "
                        "image and uses a hidden reference anchor for the default "
                        "short-window handoff. boundary_switch ends the current "
                        "image and starts the next as an independent hard cut."
                    ),
                ),
                io.Combo.Input(
                    "bridge_span",
                    options=["short_window", "full_interval"],
                    default="short_window",
                    tooltip=(
                        "Used by first_last_bridge. short_window limits next-image "
                        "influence to the final bridge window, then crops 22 "
                        "generated destination lead-in frames and one exact hidden "
                        "anchor. full_interval uses the final available task before "
                        "the transition. Ignored by boundary_switch."
                    ),
                ),
                io.Float.Input(
                    "bridge_window_seconds",
                    default=2.0,
                    min=0.0,
                    max=10.0,
                    step=0.05,
                    tooltip=(
                        "Length of the visible short_window bridge before each "
                        "transition. At 2.0, bridge motion begins two seconds early. "
                        "Zero produces a hard switch. Ignored by boundary_switch."
                    ),
                ),
                io.Combo.Input(
                    "cropped_lookahead_conditioning",
                    options=["audio_only", "future_image"],
                    default="audio_only",
                    tooltip=(
                        "short_window only. audio_only leaves cropped trailing "
                        "lookahead without a future-image anchor. future_image "
                        "adds that anchor only inside discarded frames; it never "
                        "appears in the retained output."
                    ),
                ),
                io.Int.Input(
                    "max_render_frames",
                    default=_MAX_RENDER_FRAMES,
                    min=_MIN_RENDER_FRAMES,
                    max=_MAX_RENDER_FRAMES,
                    step=17,
                    tooltip=(
                        "Maximum frames per H3 task, from 124 through 362 on the "
                        "17k+5 grid. Lower values reduce per-task memory use but "
                        "create more tasks. The planner still uses the shortest "
                        "valid length that fits each task."
                    ),
                ),
                io.Boolean.Input(
                    "align_to_activity_gap",
                    default=True,
                    tooltip=(
                        "Enabled: move each multi-image target to a detected gap "
                        "inside the search radius. With one image, use eligible "
                        "gaps for continuation seams. Disabled: retain exact manual "
                        "or evenly spaced transition frames."
                    ),
                ),
                io.Combo.Input(
                    "transition_edge",
                    options=["activity_resume", "silence_start"],
                    default="activity_resume",
                    tooltip=(
                        "activity_resume changes images when sustained sound returns "
                        "after a gap. silence_start changes images when the detected "
                        "low-activity gap begins."
                    ),
                ),
                io.Float.Input(
                    "search_window_seconds",
                    default=5.0,
                    min=0.0,
                    max=10.0,
                    step=0.05,
                    tooltip=(
                        "Multi-image only. Search this many seconds before and after "
                        "each manual or evenly spaced target. For example, 5.0 "
                        "searches from target - 5 seconds to target + 5 seconds."
                    ),
                ),
                io.Float.Input(
                    "min_gap_duration",
                    default=0.25,
                    min=0.02,
                    max=5.0,
                    step=0.02,
                    tooltip=(
                        "How long low activity must last to count as a gap. Raise "
                        "this to ignore brief dips; lower it to accept shorter pauses."
                    ),
                ),
                io.Float.Input(
                    "resume_hold_duration",
                    default=0.15,
                    min=0.02,
                    max=5.0,
                    step=0.02,
                    tooltip=(
                        "Used by activity_resume. Sound must remain active for this "
                        "long after a gap. Raise it to reject brief noises; lower it "
                        "to accept short resumed phrases."
                    ),
                ),
                io.Audio.Input(
                    "conditioning_audio",
                    optional=True,
                    tooltip=(
                        "Optional guide audio for H3 at its native sample rate. "
                        "Connect the raw Demucs vocal stem here to reduce "
                        "instrument-driven lip motion. When disconnected, H3 uses "
                        "the master audio. Duration may differ from the master by "
                        "at most one 24 FPS frame."
                    ),
                ),
            ],
            outputs=[
                io.Custom(_PLAN_KIND).Output(
                    "plan",
                    tooltip=(
                        "Version-4 H3 task plan. Connect this to MiniMax H3 Audio "
                        "Plan Step."
                    ),
                ),
                io.Int.Output(
                    "extension_task_count",
                    tooltip=(
                        "Number of tasks after the opening task; use this as the "
                        "extension-loop iteration count."
                    ),
                ),
                io.Int.Output(
                    "total_frames",
                    tooltip=(
                        "Exact retained output length at 24 FPS, derived from the "
                        "master-audio duration."
                    ),
                ),
                io.Int.Output(
                    "base_keep_frames",
                    tooltip="Frames retained from the opening H3 task.",
                ),
                io.String.Output(
                    "report",
                    tooltip=(
                        "Readable summary of image ownership, transition frames, "
                        "hidden-anchor handoffs, activity alignment, audio roles, "
                        "and task ranges."
                    ),
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        audio,
        image_batch,
        manual_transition_times,
        transition_mode,
        bridge_span,
        bridge_window_seconds,
        cropped_lookahead_conditioning,
        max_render_frames,
        align_to_activity_gap,
        transition_edge,
        search_window_seconds,
        min_gap_duration,
        resume_hold_duration,
        audio_encoder_output=None,
        conditioning_audio=None,
    ):
        manual_transition_times = unwrap_value(manual_transition_times, "")
        transition_mode = unwrap_value(transition_mode, "first_last_bridge")
        bridge_span = unwrap_value(bridge_span, "short_window")
        bridge_window_seconds = unwrap_value(bridge_window_seconds, 2.0)
        cropped_lookahead_conditioning = unwrap_value(
            cropped_lookahead_conditioning, "audio_only"
        )
        max_render_frames = unwrap_value(max_render_frames, _MAX_RENDER_FRAMES)
        align_to_activity_gap = unwrap_value(align_to_activity_gap, True)
        transition_edge = unwrap_value(transition_edge, "activity_resume")
        search_window_seconds = unwrap_value(search_window_seconds, 5.0)
        min_gap_duration = unwrap_value(min_gap_duration, 0.25)
        resume_hold_duration = unwrap_value(resume_hold_duration, 0.15)

        if not isinstance(manual_transition_times, str):
            raise TypeError("manual_transition_times must be a string.")
        if transition_mode not in ("first_last_bridge", "boundary_switch"):
            raise ValueError(f"Unsupported transition_mode: {transition_mode}")
        if bridge_span not in ("short_window", "full_interval"):
            raise ValueError(f"Unsupported bridge_span: {bridge_span}")
        if (
            not isinstance(bridge_window_seconds, (int, float))
            or isinstance(bridge_window_seconds, bool)
            or not math.isfinite(bridge_window_seconds)
            or not 0 <= bridge_window_seconds <= 10
        ):
            raise ValueError("bridge_window_seconds must be from 0.0 to 10.0.")
        bridge_window_seconds = float(bridge_window_seconds)
        if cropped_lookahead_conditioning not in ("audio_only", "future_image"):
            raise ValueError(
                "cropped_lookahead_conditioning must be audio_only or future_image."
            )
        if (
            not isinstance(max_render_frames, int)
            or isinstance(max_render_frames, bool)
            or not _is_h3_frame_count(max_render_frames)
            or not _MIN_RENDER_FRAMES <= max_render_frames <= _MAX_RENDER_FRAMES
        ):
            raise ValueError(
                "max_render_frames must follow H3's 17k+5 grid from 124 to 362."
            )
        if transition_edge not in ("activity_resume", "silence_start"):
            raise ValueError(f"Unsupported transition_edge: {transition_edge}")
        if search_window_seconds < 0:
            raise ValueError("search_window_seconds cannot be negative.")
        if min_gap_duration <= 0 or resume_hold_duration <= 0:
            raise ValueError("Gap and resume durations must be greater than zero.")

        images = flatten_images(image_batch)
        image_count = len(images)
        if image_count == 0:
            raise ValueError("image_batch must contain at least one image.")

        waveform, sample_rate = _audio_shape(audio)
        duration = audio_duration_seconds(audio)
        guide_audio = audio if conditioning_audio is None else conditioning_audio
        conditioning_waveform, conditioning_sample_rate = _audio_shape(guide_audio)
        conditioning_duration = audio_duration_seconds(guide_audio)
        if abs(conditioning_duration - duration) > 1.0 / _H3_FPS:
            raise ValueError(
                "conditioning_audio duration must match the master audio within "
                "one 24 FPS video frame."
            )
        total_frames = math.ceil(duration * _H3_FPS)
        if total_frames < 1:
            raise ValueError("Audio must contain at least one output video frame.")

        report_lines = [
            f"Timeline: {total_frames} frames at 24 fps ({duration:.3f}s)",
            f"Reference images: {image_count}; mode: {transition_mode}",
            (
                f"Adaptive H3 range: {_MIN_RENDER_FRAMES}-{max_render_frames}; "
                f"continuity overlap: {_OVERLAP_FRAMES}"
            ),
            f"Audio: {waveform.shape[-1]} samples at {sample_rate} Hz",
            (
                "H3 conditioning audio: "
                f"{conditioning_waveform.shape[-1]} samples at "
                f"{conditioning_sample_rate} Hz "
                f"({'master fallback' if conditioning_audio is None else 'separate input'})"
            ),
        ]
        if transition_mode == "boundary_switch":
            report_lines.append(
                "Boundary switch: bridge and cropped-lookahead controls ignored."
            )
        else:
            report_lines.append(
                f"Bridge span: {bridge_span}; window: {bridge_window_seconds:g}s "
                f"({round(bridge_window_seconds * _H3_FPS)} frames); "
                f"cropped lookahead: {cropped_lookahead_conditioning}"
            )
            if bridge_span == "short_window" and bridge_window_seconds > 0:
                report_lines.append(
                    "Short-window destinations crop 22 generated lead-in frames "
                    "plus one hidden reference anchor; ordinary extensions keep "
                    "exactly 22 generated continuity frames."
                )

        if image_count == 1:
            transition_source = "single_reference"
            manual_times: list[float] = []
            target_frames: list[int] = []
            report_lines.append(
                "Single image: the source initializes task 0 and remains the "
                "visual prompt image for later retained spans; cropped-lookahead "
                "conditioning is ignored."
            )
            if manual_transition_times.strip():
                report_lines.append("Single image: manual transition times ignored.")
        else:
            manual_data = _parse_manual_transition_times(
                manual_transition_times, image_count, total_frames
            )
            if manual_data is None:
                transition_source = "evenly_spaced"
                manual_times = []
                target_frames = [
                    round(total_frames * index / image_count)
                    for index in range(1, image_count)
                ]
                report_lines.append(f"Even transition frames: {target_frames}")
            else:
                transition_source = "manual_seconds"
                manual_times, target_frames = manual_data
                report_lines.append(f"Manual transition seconds: {manual_times}")
                report_lines.append(f"Manual transition frames: {target_frames}")

        candidates = list(target_frames)
        continuation_boundaries: list[int] = []
        if image_count == 1 and not align_to_activity_gap:
            report_lines.append(
                "Single-image activity-gap continuation disabled; H3 maximum-span "
                "boundaries retained."
            )
        elif image_count == 1:
            activity, activity_error = encoded_audio_activity(audio_encoder_output)
            if activity is None:
                report_lines.append(
                    "Single-image continuation alignment fallback: "
                    f"{activity_error}; H3 maximum-span boundaries retained."
                )
            else:
                report_lines.append(
                    "Single-image mode scans eligible continuation ranges for "
                    "sentence gaps; search_window_seconds applies only to "
                    "multi-image transition targets."
                )
                report_lines.append(
                    f"Gap controls: minimum gap {min_gap_duration:.3f}s; "
                    f"sustained resume {resume_hold_duration:.3f}s."
                )
                continuation_boundaries, continuation_notes = (
                    _single_image_gap_boundaries(
                        activity,
                        total_frames,
                        transition_edge,
                        min_gap_duration,
                        resume_hold_duration,
                        max_render_frames,
                    )
                )
                report_lines.extend(continuation_notes)
                if not continuation_boundaries:
                    report_lines.append(
                        "Single-image timeline needs no eligible continuation cut."
                    )
        elif not align_to_activity_gap:
            report_lines.append(
                "Encoded-audio activity alignment disabled; target frames retained."
            )
        else:
            activity, activity_error = encoded_audio_activity(audio_encoder_output)
            if activity is None:
                report_lines.append(
                    "Encoded-audio alignment fallback: "
                    f"{activity_error}; target frames retained."
                )
            else:
                report_lines.append(
                    "Transition alignment reuses InfiniteTalk Wav2Vec features as "
                    "an activity estimate."
                )
                report_lines.append(
                    f"Alignment controls: search +/-{search_window_seconds:.3f}s; "
                    f"minimum gap {min_gap_duration:.3f}s; sustained resume "
                    f"{resume_hold_duration:.3f}s."
                )
                for index, target_frame in enumerate(target_frames):
                    aligned_frame, details = align_activity_gap(
                        activity,
                        target_frame,
                        _H3_FPS,
                        transition_edge,
                        search_window_seconds,
                        min_gap_duration,
                        resume_hold_duration,
                        minimum_frame=_OVERLAP_FRAMES if index == 0 else None,
                        maximum_frame=total_frames - 1,
                    )
                    if aligned_frame is None:
                        report_lines.append(
                            f"Transition {index + 1}: frame {target_frame} "
                            f"retained; {details}."
                        )
                    else:
                        candidates[index] = aligned_frame
                        report_lines.append(
                            f"Transition {index + 1}: frame {target_frame} -> "
                            f"{aligned_frame} ({transition_edge}); {details}."
                        )

        transition_frames, adjustments = _space_transition_frames(
            candidates, total_frames
        )
        report_lines.extend(f"Spacing adjustment: {item}." for item in adjustments)
        tasks = _build_plan_tasks(
            transition_frames,
            continuation_boundaries,
            total_frames,
            waveform.shape[-1],
            sample_rate,
            conditioning_waveform.shape[-1],
            conditioning_sample_rate,
            image_count,
            transition_mode,
            bridge_span,
            round(bridge_window_seconds * _H3_FPS),
            cropped_lookahead_conditioning,
            max_render_frames,
        )

        plan: dict[str, Any] = {
            "kind": _PLAN_KIND,
            "version": _PLAN_VERSION,
            "fps": _H3_FPS,
            "sample_rate": sample_rate,
            "sample_count": waveform.shape[-1],
            "conditioning_sample_rate": conditioning_sample_rate,
            "conditioning_sample_count": conditioning_waveform.shape[-1],
            "total_frames": total_frames,
            "image_count": image_count,
            "transition_source": transition_source,
            "manual_transition_times": manual_times,
            "transition_frames": transition_frames,
            "continuation_frames": (
                [int(task["output_end_frame"]) for task in tasks[:-1]]
                if image_count == 1
                else []
            ),
            "transition_mode": transition_mode,
            "bridge_span": bridge_span,
            "bridge_window_seconds": bridge_window_seconds,
            "cropped_lookahead_conditioning": cropped_lookahead_conditioning,
            "max_render_frames": max_render_frames,
            "min_render_frames": _MIN_RENDER_FRAMES,
            "overlap_frames": _OVERLAP_FRAMES,
            "align_to_activity_gap": align_to_activity_gap,
            "transition_edge": transition_edge,
            "search_window_seconds": search_window_seconds,
            "min_gap_duration": min_gap_duration,
            "resume_hold_duration": resume_hold_duration,
            "tasks": tasks,
        }
        render_lengths = sorted({int(task["render_frames"]) for task in tasks})
        report_lines.append(
            f"Tasks: {len(tasks)} total, {max(0, len(tasks) - 1)} extensions"
        )
        if transition_frames:
            report_lines.append(
                "Destination ownership: "
                + ", ".join(
                    f"frame {frame} -> image {image_index}"
                    for image_index, frame in enumerate(transition_frames, start=1)
                )
            )
        report_lines.append(f"Adaptive render lengths: {render_lengths}")
        report_lines.append(
            "Output ranges: "
            + ", ".join(
                f"[{task['output_start_frame']},{task['output_end_frame']})/"
                f"image {task['prompt_image_index']}"
                for task in tasks
            )
        )
        report = "\n".join(report_lines)
        log.debug(_LOG_PREFIX, report)
        return io.NodeOutput(
            plan,
            max(0, len(tasks) - 1),
            total_frames,
            int(tasks[0]["keep_frames"]),
            report,
        )
