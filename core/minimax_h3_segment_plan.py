"""Planning and validation for the additive MiniMax H3 segmented timeline V2."""

import math
from bisect import bisect_right
from itertools import pairwise
from typing import Any

import torch  # type: ignore

from .audio_timeline import align_activity_gap

PLAN_KIND = "MINIMAX_H3_SEGMENT_PLAN"
PLAN_VERSION = 1
H3_FPS = 24.0
MIN_RENDER_FRAMES = 124
MAX_RENDER_FRAMES = 362
CONTINUITY_FRAMES = 22

CONDITIONING_FAMILIES = ("fl2va_keyframes", "ref2va_active_reference")
SEGMENT_STRATEGIES = ("hard_cut", "first_last_bridge", "warmup_reset")
RESET_ANCHORS = ("first_only", "same_first_last_experimental")
TECHNICAL_SPLIT_SOURCES = ("original_image_reset", "generated_continuation")
TECHNICAL_SEAM_STYLES = ("plain_reset", "intentional_camera_cut")
DEFAULT_TECHNICAL_CUT_INSTRUCTION = (
    "During the hidden lead-in, rapidly orbit to a strong left-side three-quarter "
    "camera angle and dolly far backward. At the cut, hold a waist-up medium-wide "
    "shot, visibly different from the prior frontal close-up. Preserve identity, "
    "wardrobe, scene, lighting, and ongoing action; do not show a frontal close-up."
)
REF_IMAGE_SIZES = ("match", "max")


def is_h3_frame_count(frame_count: int) -> bool:
    return (
        MIN_RENDER_FRAMES <= frame_count <= MAX_RENDER_FRAMES
        and frame_count % 17 == 5
    )


def aligned_h3_length(required_frames: int) -> int:
    required_frames = max(MIN_RENDER_FRAMES, required_frames)
    return required_frames + (5 - required_frames) % 17


def frame_to_sample(frame: int, sample_rate: int) -> int:
    return round(frame / H3_FPS * sample_rate)


def audio_shape(audio: Any) -> tuple[torch.Tensor, int]:
    if not isinstance(audio, dict):
        raise ValueError("audio must be a valid ComfyUI AUDIO value.")  # noqa: TRY004
    waveform = audio.get("waveform")
    sample_rate = audio.get("sample_rate")
    if not isinstance(waveform, torch.Tensor) or waveform.ndim < 1:
        raise ValueError("audio waveform is missing or invalid.")
    if (
        not isinstance(sample_rate, (int, float))
        or isinstance(sample_rate, bool)
        or sample_rate <= 0
        or not float(sample_rate).is_integer()
    ):
        raise ValueError("audio sample rate must be a positive integer value.")
    return waveform, int(sample_rate)


def parse_manual_transition_times(
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
            "manual_transition_times must contain one time for every image "
            "after the first."
        )
    try:
        times = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(
            "manual_transition_times must contain comma-separated seconds."
        ) from error
    if any(not math.isfinite(item) for item in times):
        raise ValueError("manual_transition_times must contain finite values.")
    if any(item <= 0 for item in times):
        raise ValueError("manual transition times must be greater than zero.")
    if any(current <= previous for previous, current in pairwise(times)):
        raise ValueError("manual transition times must be strictly increasing.")
    frames = [round(item * H3_FPS) for item in times]
    if any(frame >= total_frames for frame in frames):
        raise ValueError("manual transition times must be inside the audio timeline.")
    if len(set(frames)) != len(frames):
        raise ValueError("manual transition times resolve to duplicate video frames.")
    return times, frames


def space_transition_frames(
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
        resolved = max(minimum, min(candidate, maximum))
        if resolved != candidate:
            notes.append(f"transition {index + 1}: frame {candidate} -> {resolved}")
        adjusted.append(resolved)
    return adjusted, notes


def single_image_gap_boundaries(
    activity: torch.Tensor,
    total_frames: int,
    transition_edge: str,
    min_gap_duration: float,
    resume_hold_duration: float,
    max_visible_frames: int,
) -> tuple[list[int], list[str]]:
    """Choose sentence-gap seams while a single-image timeline exceeds capacity."""
    boundaries: list[int] = []
    notes: list[str] = []
    start = 0
    while total_frames - start > max_visible_frames:
        target = start + max_visible_frames
        minimum = min(target, start + H3_FPS)
        aligned, details = align_activity_gap(
            activity,
            target,
            H3_FPS,
            transition_edge,
            max(0.0, (target - minimum) / H3_FPS),
            min_gap_duration,
            resume_hold_duration,
            minimum_frame=minimum,
            maximum_frame=target,
        )
        seam = target if aligned is None else aligned
        reason = "maximum visible span" if aligned is None else transition_edge
        notes.append(
            f"technical seam {len(boundaries) + 1}: frame {seam} "
            f"({reason}); {details}."
        )
        boundaries.append(seam)
        start = seam
    return boundaries, notes


def _task_prefix(
    *,
    is_first_task: bool,
    is_transition_start: bool,
    is_technical_start: bool,
    conditioning_family: str,
    segment_strategy: str,
    technical_split_source: str,
    technical_seam_style: str,
    warmup_frames: int,
) -> tuple[int, bool, bool, str]:
    if conditioning_family == "ref2va_active_reference":
        return warmup_frames, False, False, "reference_warmup"
    if is_first_task:
        return 0, True, False, "initial_source"
    if is_transition_start:
        if segment_strategy == "hard_cut":
            return 0, True, False, "hard_cut"
        return warmup_frames, True, False, "destination_warmup"
    if is_technical_start and technical_split_source == "generated_continuation":
        return CONTINUITY_FRAMES, False, True, "generated_continuation"
    if is_technical_start and technical_seam_style == "intentional_camera_cut":
        return warmup_frames, True, False, "technical_original_warmup"
    if segment_strategy == "hard_cut":
        return 0, True, False, "technical_original_reset"
    return warmup_frames, True, False, "technical_original_warmup"


def _task_count_layout(
    retained_frames: int,
    first_prefix: int,
    later_prefix: int,
    first_has_start: bool,
    later_has_start: bool,
    has_real_endpoint: bool,
    same_endpoint: bool,
    max_render_frames: int,
) -> tuple[list[int], list[int]]:
    task_count = 1
    while task_count <= retained_frames:
        prefixes = [first_prefix, *([later_prefix] * (task_count - 1))]
        tails: list[int] = []
        for index in range(task_count):
            real = has_real_endpoint and index == task_count - 1
            has_start = first_has_start if index == 0 else later_has_start
            tails.append(1 if real or (same_endpoint and has_start) else 0)
        capacities = [
            max_render_frames - prefix - tail
            for prefix, tail in zip(prefixes, tails)
        ]
        if min(capacities) >= 1 and sum(capacities) >= retained_frames:
            return prefixes, tails
        task_count += 1
    raise ValueError(
        "The selected warmup/endpoints leave no visible capacity. Increase "
        "max_render_frames or reduce warmup_seconds."
    )


def _balanced_keeps(retained_frames: int, capacities: list[int]) -> list[int]:
    keeps: list[int] = []
    remaining = retained_frames
    for index, capacity in enumerate(capacities):
        remaining_tasks = len(capacities) - index
        later_capacity = sum(capacities[index + 1 :])
        minimum = max(1, remaining - later_capacity)
        maximum = min(capacity, remaining - (remaining_tasks - 1))
        desired = round(remaining / remaining_tasks)
        keep = max(minimum, min(desired, maximum))
        keeps.append(keep)
        remaining -= keep
    if remaining:
        raise RuntimeError("Balanced H3 task splitting did not consume its span.")
    return keeps


def _audio_metadata(
    render_start: int,
    render_frames: int,
    conditioning_sample_count: int,
    conditioning_sample_rate: int,
) -> dict[str, int]:
    requested_start = frame_to_sample(render_start, conditioning_sample_rate)
    requested_end = frame_to_sample(
        render_start + render_frames, conditioning_sample_rate
    )
    return {
        "audio_start_sample": max(
            0, min(conditioning_sample_count, requested_start)
        ),
        "audio_end_sample": max(0, min(conditioning_sample_count, requested_end)),
        "audio_pad_start_samples": max(0, -requested_start),
        "audio_pad_end_samples": max(
            0, requested_end - conditioning_sample_count
        ),
    }


def build_segment_tasks(
    *,
    transition_frames: list[int],
    continuation_frames: list[int],
    total_frames: int,
    image_count: int,
    sample_count: int,
    sample_rate: int,
    conditioning_sample_count: int,
    conditioning_sample_rate: int,
    conditioning_family: str,
    segment_strategy: str,
    warmup_frames: int,
    reset_anchor: str,
    technical_split_source: str,
    technical_seam_style: str,
    ref_image_size: str,
    max_render_frames: int,
) -> list[dict[str, Any]]:
    interval_edges = [0, *transition_frames, total_frames]
    regions: list[tuple[int, int, int, bool, bool]] = []
    if image_count == 1 and continuation_frames:
        starts = [0, *continuation_frames]
        ends = [*continuation_frames, total_frames]
        regions = [
            (start, end, 0, index == 0, index > 0)
            for index, (start, end) in enumerate(zip(starts, ends))
        ]
    else:
        regions = [
            (start, end, owner, owner == 0, False)
            for owner, (start, end) in enumerate(pairwise(interval_edges))
        ]

    tasks: list[dict[str, Any]] = []
    for region_start, region_end, owner, is_first_region, forced_technical in regions:
        retained_frames = region_end - region_start
        is_transition_start = owner > 0 and region_start == transition_frames[owner - 1]
        first_technical = forced_technical
        first_prefix, first_has_start, first_continuity, first_role = _task_prefix(
            is_first_task=is_first_region and not tasks,
            is_transition_start=is_transition_start,
            is_technical_start=first_technical,
            conditioning_family=conditioning_family,
            segment_strategy=segment_strategy,
            technical_split_source=technical_split_source,
            technical_seam_style=technical_seam_style,
            warmup_frames=warmup_frames,
        )
        later_prefix, later_has_start, later_continuity, later_role = _task_prefix(
            is_first_task=False,
            is_transition_start=False,
            is_technical_start=True,
            conditioning_family=conditioning_family,
            segment_strategy=segment_strategy,
            technical_split_source=technical_split_source,
            technical_seam_style=technical_seam_style,
            warmup_frames=warmup_frames,
        )
        has_real_endpoint = (
            segment_strategy == "first_last_bridge" and owner < image_count - 1
        )
        same_endpoint = (
            conditioning_family == "fl2va_keyframes"
            and reset_anchor == "same_first_last_experimental"
            and not has_real_endpoint
        )
        prefixes, tails = _task_count_layout(
            retained_frames,
            first_prefix,
            later_prefix,
            first_has_start,
            later_has_start,
            has_real_endpoint,
            same_endpoint,
            max_render_frames,
        )
        capacities = [
            max_render_frames - prefix - tail
            for prefix, tail in zip(prefixes, tails)
        ]
        keeps = _balanced_keeps(retained_frames, capacities)
        output_start = region_start
        for region_task_index, (prefix, tail, keep_frames) in enumerate(
            zip(prefixes, tails, keeps)
        ):
            is_region_first = region_task_index == 0
            is_region_last = region_task_index == len(prefixes) - 1
            if is_region_first:
                has_start = first_has_start
                has_continuity = first_continuity
                role = first_role
            else:
                has_start = later_has_start
                has_continuity = later_continuity
                role = later_role
            real_endpoint = has_real_endpoint and is_region_last
            endpoint_image = owner + 1 if real_endpoint else owner
            has_last = real_endpoint or (same_endpoint and has_start)
            if real_endpoint:
                role = "first_last_bridge"
            render_frames = aligned_h3_length(prefix + keep_frames + tail)
            if render_frames > max_render_frames:
                raise ValueError("A balanced task exceeds max_render_frames.")
            output_end = output_start + keep_frames
            render_start = output_start - prefix
            endpoint_index = prefix + keep_frames if has_last else None
            task = {
                "task_index": len(tasks),
                "task_role": role,
                "render_start_frame": render_start,
                "render_frames": render_frames,
                "output_start_frame": output_start,
                "output_end_frame": output_end,
                "crop_start_frames": prefix,
                "keep_frames": keep_frames,
                "trailing_crop_frames": render_frames - prefix - keep_frames,
                "source_image_index": owner,
                "expected_image_owner": owner,
                "prompt_index": owner,
                "conditioning_family": conditioning_family,
                "ref_image_size": ref_image_size,
                "has_continuity": has_continuity,
                "has_start_guide": has_start,
                "start_guide_frame_index": 0 if has_start else None,
                "has_last_image": has_last,
                "last_image_index": endpoint_image if has_last else None,
                "endpoint_frame_index": endpoint_index,
                "endpoint_output_frame": output_end if has_last else None,
                "endpoint_role": (
                    "destination" if real_endpoint else "same_source_experimental"
                )
                if has_last
                else None,
                "starts_image_interval": is_transition_start and is_region_first,
                "is_technical_seam": bool(tasks) and not (
                    is_transition_start and is_region_first
                ),
                "is_final": output_end == total_frames,
                "output_start_sample": min(
                    sample_count, frame_to_sample(output_start, sample_rate)
                ),
                "output_end_sample": min(
                    sample_count, frame_to_sample(output_end, sample_rate)
                ),
                **_audio_metadata(
                    render_start,
                    render_frames,
                    conditioning_sample_count,
                    conditioning_sample_rate,
                ),
            }
            tasks.append(task)
            output_start = output_end
    return tasks


def task_uses_intentional_camera_cut(
    plan: dict[str, Any], task: dict[str, Any]
) -> bool:
    """Return whether one task receives the opt-in technical-cut prompt."""
    return (
        plan.get("technical_seam_style", "plain_reset")
        == "intentional_camera_cut"
        and plan.get("technical_split_source") == "original_image_reset"
        and task.get("is_technical_seam") is True
        and task.get("starts_image_interval") is False
        and task.get("has_continuity") is False
        and task.get("endpoint_role") != "destination"
    )


def build_analysis_manifest(plan: dict[str, Any]) -> dict[str, Any]:
    tasks = plan["tasks"]
    transitions = []
    for destination, frame in enumerate(plan["transition_frames"], start=1):
        bridge_task = next(
            (
                task
                for task in tasks
                if task["endpoint_role"] == "destination"
                and task["last_image_index"] == destination
                and task["endpoint_output_frame"] == frame
            ),
            None,
        )
        transitions.append(
            {
                "frame": frame,
                "from_image_index": destination - 1,
                "to_image_index": destination,
                "strategy": plan["segment_strategy"],
                "permitted_destination_start_frame": (
                    bridge_task["output_start_frame"] if bridge_task else frame
                ),
                "source_prompt_index": destination - 1,
                "destination_prompt_index": destination,
            }
        )
    return {
        "kind": "MINIMAX_H3_SEGMENT_ANALYSIS",
        "schema_version": 1,
        "fps": plan["fps"],
        "total_frames": plan["total_frames"],
        "sample_rate": plan["sample_rate"],
        "sample_count": plan["sample_count"],
        "conditioning_family": plan["conditioning_family"],
        "segment_strategy": plan["segment_strategy"],
        "technical_seam_style": plan.get(
            "technical_seam_style", "plain_reset"
        ),
        "planned_image_transitions": transitions,
        "task_seams": [
            {
                "frame": task["output_start_frame"],
                "task_index": task["task_index"],
                "seam_kind": (
                    "image_transition"
                    if task["starts_image_interval"]
                    else "technical_split"
                ),
                "expected_image_owner": task["expected_image_owner"],
                "technical_seam_style": (
                    "intentional_camera_cut"
                    if task_uses_intentional_camera_cut(plan, task)
                    else "plain_reset"
                )
                if task["is_technical_seam"]
                else None,
            }
            for task in tasks[1:]
        ],
        "retained_ranges": [
            {
                "task_index": task["task_index"],
                "start_frame": task["output_start_frame"],
                "end_frame": task["output_end_frame"],
                "expected_image_owner": task["expected_image_owner"],
                "prompt_index": task["prompt_index"],
            }
            for task in tasks
        ],
        "prompt_owners": [
            {"task_index": task["task_index"], "prompt_index": task["prompt_index"]}
            for task in tasks
        ],
        "guide_positions": [
            {
                "task_index": task["task_index"],
                "source_image_index": task["source_image_index"],
                "start_guide_frame_index": task["start_guide_frame_index"],
                "last_image_index": task["last_image_index"],
                "endpoint_frame_index": task["endpoint_frame_index"],
                "endpoint_role": task["endpoint_role"],
            }
            for task in tasks
        ],
        "expected_source_ownership": [
            {
                "start_frame": start,
                "end_frame": end,
                "image_index": index,
            }
            for index, (start, end) in enumerate(
                pairwise([0, *plan["transition_frames"], plan["total_frames"]])
            )
        ],
    }


def _require_int(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Plan field '{key}' must be an integer.")
    return value


def _optional_int(mapping: dict[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Plan field '{key}' must be an integer or null.")
    return value


def _require_bool(mapping: dict[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise TypeError(f"Plan field '{key}' must be boolean.")
    return value


def validate_segment_plan(plan: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(plan, dict):
        raise TypeError(f"plan must be a {PLAN_KIND} dictionary.")
    if plan.get("kind") != PLAN_KIND or plan.get("version") != PLAN_VERSION:
        raise ValueError(f"Unsupported or invalid {PLAN_KIND} metadata.")
    if plan.get("fps") != H3_FPS:
        raise ValueError("Plan fps must be MiniMax H3's native 24 FPS.")
    image_count = _require_int(plan, "image_count")
    total_frames = _require_int(plan, "total_frames")
    sample_rate = _require_int(plan, "sample_rate")
    sample_count = _require_int(plan, "sample_count")
    conditioning_rate = _require_int(plan, "conditioning_sample_rate")
    conditioning_count = _require_int(plan, "conditioning_sample_count")
    max_render = _require_int(plan, "max_render_frames")
    if image_count < 1 or total_frames < 1:
        raise ValueError("Plan image_count and total_frames must be positive.")
    if min(sample_rate, sample_count, conditioning_rate, conditioning_count) < 1:
        raise ValueError("Plan audio metadata must be positive.")
    if not is_h3_frame_count(max_render):
        raise ValueError("Plan max_render_frames is not a valid H3 render limit.")
    if plan.get("conditioning_family") not in CONDITIONING_FAMILIES:
        raise ValueError("Plan conditioning_family is invalid.")
    if plan.get("segment_strategy") not in SEGMENT_STRATEGIES:
        raise ValueError("Plan segment_strategy is invalid.")
    if plan.get("reset_anchor") not in RESET_ANCHORS:
        raise ValueError("Plan reset_anchor is invalid.")
    if plan.get("technical_split_source") not in TECHNICAL_SPLIT_SOURCES:
        raise ValueError("Plan technical_split_source is invalid.")
    technical_seam_style = plan.get("technical_seam_style", "plain_reset")
    if technical_seam_style not in TECHNICAL_SEAM_STYLES:
        raise ValueError("Plan technical_seam_style is invalid.")
    technical_cut_instruction = plan.get("technical_cut_instruction")
    if technical_cut_instruction is not None and (
        not isinstance(technical_cut_instruction, str)
        or not technical_cut_instruction.strip()
    ):
        raise ValueError("Plan technical_cut_instruction must not be blank.")
    if technical_seam_style == "intentional_camera_cut":
        if plan["technical_split_source"] != "original_image_reset":
            raise ValueError(
                "Intentional camera cuts require original_image_reset technical splits."
            )
        if not isinstance(technical_cut_instruction, str):
            raise ValueError(
                "Intentional camera cuts require a technical_cut_instruction."
            )
    if plan.get("ref_image_size") not in REF_IMAGE_SIZES:
        raise ValueError("Plan ref_image_size is invalid.")
    transitions = plan.get("transition_frames")
    if not isinstance(transitions, list) or any(
        not isinstance(frame, int) or isinstance(frame, bool) for frame in transitions
    ):
        raise TypeError("Plan transition_frames must be a list of integers.")
    if len(transitions) != image_count - 1:
        raise ValueError("Plan transition_frames do not match image_count.")
    if transitions != sorted(set(transitions)) or any(
        frame < 1 or frame >= total_frames for frame in transitions
    ):
        raise ValueError("Plan transition_frames are outside the retained timeline.")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Plan tasks must be a non-empty list.")

    expected_start = 0
    destination_endpoints: list[tuple[int, int, int]] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise TypeError(f"Plan task {index} must be a dictionary.")
        if _require_int(task, "task_index") != index:
            raise ValueError(f"Plan task {index} has a mismatched task_index.")
        output_start = _require_int(task, "output_start_frame")
        output_end = _require_int(task, "output_end_frame")
        render_start = _require_int(task, "render_start_frame")
        render_frames = _require_int(task, "render_frames")
        crop_start = _require_int(task, "crop_start_frames")
        keep_frames = _require_int(task, "keep_frames")
        trailing_crop = _require_int(task, "trailing_crop_frames")
        owner = _require_int(task, "source_image_index")
        prompt_index = _require_int(task, "prompt_index")
        has_continuity = _require_bool(task, "has_continuity")
        has_start = _require_bool(task, "has_start_guide")
        has_last = _require_bool(task, "has_last_image")
        start_index = _optional_int(task, "start_guide_frame_index")
        last_index = _optional_int(task, "last_image_index")
        endpoint_index = _optional_int(task, "endpoint_frame_index")
        endpoint_output = _optional_int(task, "endpoint_output_frame")
        endpoint_role = task.get("endpoint_role")
        if output_start != expected_start or output_end <= output_start:
            raise ValueError(f"Plan task {index} is not timeline-contiguous.")
        if keep_frames != output_end - output_start:
            raise ValueError(f"Plan task {index} has invalid keep metadata.")
        if render_start != output_start - crop_start:
            raise ValueError(f"Plan task {index} has invalid render_start_frame.")
        if not is_h3_frame_count(render_frames) or render_frames > max_render:
            raise ValueError(f"Plan task {index} has an invalid H3 render length.")
        if trailing_crop != render_frames - crop_start - keep_frames:
            raise ValueError(f"Plan task {index} has invalid trailing crop metadata.")
        if min(crop_start, keep_frames, trailing_crop) < 0:
            raise ValueError(f"Plan task {index} has a negative frame range.")
        planned_owner = bisect_right(transitions, output_start)
        owner_end = (
            transitions[planned_owner]
            if planned_owner < len(transitions)
            else total_frames
        )
        if (
            not 0 <= owner < image_count
            or prompt_index != owner
            or owner != planned_owner
            or task.get("expected_image_owner") != owner
            or output_end > owner_end
        ):
            raise ValueError(f"Plan task {index} has invalid source/prompt ownership.")
        continuation_role = task.get("task_role") == "generated_continuation"
        if has_continuity != continuation_role or (
            has_continuity
            and (crop_start != CONTINUITY_FRAMES or has_start)
        ):
            raise ValueError(f"Plan task {index} has invalid continuity metadata.")
        if has_start != (start_index is not None):
            raise ValueError(f"Plan task {index} has incomplete start-guide metadata.")
        if start_index is not None and start_index != 0:
            raise ValueError(f"Plan task {index} start guide must be local frame 0.")
        if has_last != (last_index is not None and endpoint_index is not None):
            raise ValueError(f"Plan task {index} has incomplete endpoint metadata.")
        if last_index is not None and not 0 <= last_index < image_count:
            raise ValueError(f"Plan task {index} has an invalid last image.")
        if endpoint_index is not None and (
            endpoint_index != crop_start + keep_frames
            or endpoint_index >= render_frames
            or endpoint_output != output_end
        ):
            raise ValueError(f"Plan task {index} endpoint is not fully discarded.")
        if endpoint_role not in (
            None,
            "destination",
            "same_source_experimental",
        ) or (endpoint_role is None) != (not has_last):
            raise ValueError(f"Plan task {index} has an invalid endpoint role.")
        if endpoint_role == "destination":
            if last_index != owner + 1 or output_end not in transitions:
                raise ValueError(f"Plan task {index} has an invalid destination endpoint.")
            destination_endpoints.append((output_end, int(last_index), index))
        elif endpoint_role == "same_source_experimental" and last_index != owner:
            raise ValueError(f"Plan task {index} has an invalid same-source endpoint.")
        starts_interval = output_start in transitions
        if task.get("starts_image_interval") is not starts_interval:
            raise ValueError(f"Plan task {index} has invalid transition-seam metadata.")
        if task.get("is_technical_seam") is not (index > 0 and not starts_interval):
            raise ValueError(f"Plan task {index} has invalid technical-seam metadata.")
        if (
            task_uses_intentional_camera_cut(plan, task)
            and plan["conditioning_family"] == "fl2va_keyframes"
            and (not has_start or start_index != 0 or crop_start < 1)
        ):
            raise ValueError(
                f"Plan task {index} exposes an intentional-cut source guide."
            )
        expected_audio = _audio_metadata(
            render_start, render_frames, conditioning_count, conditioning_rate
        )
        if any(task.get(key) != value for key, value in expected_audio.items()):
            raise ValueError(f"Plan task {index} has invalid guide-audio metadata.")
        if task.get("output_start_sample") != min(
            sample_count, frame_to_sample(output_start, sample_rate)
        ) or task.get("output_end_sample") != min(
            sample_count, frame_to_sample(output_end, sample_rate)
        ):
            raise ValueError(f"Plan task {index} has invalid output-audio metadata.")
        if task.get("conditioning_family") != plan["conditioning_family"]:
            raise ValueError(f"Plan task {index} changes conditioning family.")
        if task.get("ref_image_size") != plan["ref_image_size"]:
            raise ValueError(f"Plan task {index} changes reference sizing.")
        if _require_bool(task, "is_final") != (output_end == total_frames):
            raise ValueError(f"Plan task {index} has an invalid final flag.")
        expected_start = output_end
    if expected_start != total_frames:
        raise ValueError("Plan tasks do not cover the complete retained timeline.")
    expected_endpoints = (
        [(frame, destination, None) for destination, frame in enumerate(transitions, 1)]
        if plan["segment_strategy"] == "first_last_bridge"
        else []
    )
    if [
        (frame, destination, None)
        for frame, destination, _task_index in destination_endpoints
    ] != expected_endpoints:
        raise ValueError("Plan destination endpoints do not match image transitions.")
    for frame, destination, task_index in destination_endpoints:
        if task_index + 1 >= len(tasks):
            raise ValueError("A bridge endpoint has no following destination task.")
        following = tasks[task_index + 1]
        if (
            following["output_start_frame"] != frame
            or following["source_image_index"] != destination
            or following["has_continuity"]
        ):
            raise ValueError("A bridge endpoint is not paired with its destination task.")
    if plan["conditioning_family"] == "ref2va_active_reference" and any(
        task["has_start_guide"]
        or task["has_last_image"]
        or task["has_continuity"]
        for task in tasks
    ):
        raise ValueError("Ref2VA plans cannot contain positional image guides.")
    return plan, tasks
