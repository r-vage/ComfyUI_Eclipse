# MiniMax H3 Audio Plan Step [Eclipse]
#
# Resolves one planned H3 task into exact guide audio, image, continuity, crop,
# and hidden-anchor values without modifying the accumulated timeline.

from typing import Any

import torch  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import flatten_images, unwrap_value
from ..core.logger import log

_LOG_PREFIX = "MiniMax H3 Audio Plan Step"
_PLAN_KIND = "MINIMAX_H3_AUDIO_PLAN"
_PLAN_VERSION = 4
_H3_FPS = 24.0
_OVERLAP_FRAMES = 22
_DESTINATION_CROP_FRAMES = 23
_DESTINATION_ANCHOR_FRAME = 22


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


def _require_number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"Plan field '{key}' must be numeric.")
    return float(value)


def _frame_to_sample(frame: int, sample_rate: int) -> int:
    return round(frame / _H3_FPS * sample_rate)


def _validate_plan(plan: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(plan, dict):
        raise TypeError("plan must be a MINIMAX_H3_AUDIO_PLAN dictionary.")
    if plan.get("kind") != _PLAN_KIND or plan.get("version") != _PLAN_VERSION:
        raise ValueError("Unsupported or invalid MINIMAX_H3_AUDIO_PLAN metadata.")

    fps = plan.get("fps")
    if not isinstance(fps, (int, float)) or fps != _H3_FPS:
        raise ValueError("Plan fps must be MiniMax H3's native 24 FPS.")
    image_count = _require_int(plan, "image_count")
    total_frames = _require_int(plan, "total_frames")
    sample_rate = _require_int(plan, "sample_rate")
    sample_count = _require_int(plan, "sample_count")
    conditioning_sample_rate = _require_int(plan, "conditioning_sample_rate")
    conditioning_sample_count = _require_int(plan, "conditioning_sample_count")
    overlap_frames = _require_int(plan, "overlap_frames")
    if image_count < 1 or total_frames < 1:
        raise ValueError("Plan image_count and total_frames must be positive.")
    if (
        sample_rate < 1
        or sample_count < 1
        or conditioning_sample_rate < 1
        or conditioning_sample_count < 1
    ):
        raise ValueError("Plan audio metadata must be positive.")
    if overlap_frames != 22:
        raise ValueError("Plan overlap_frames must be 22 for this H3 workflow.")
    master_duration = sample_count / sample_rate
    conditioning_duration = conditioning_sample_count / conditioning_sample_rate
    if abs(conditioning_duration - master_duration) > 1.0 / _H3_FPS:
        raise ValueError(
            "Plan conditioning-audio duration differs from the master by more "
            "than one video frame."
        )
    if plan.get("transition_mode") not in (
        "first_last_bridge",
        "boundary_switch",
    ):
        raise ValueError("Plan transition_mode is invalid.")
    bridge_span = plan.get("bridge_span")
    if bridge_span not in ("short_window", "full_interval"):
        raise ValueError("Plan bridge_span is invalid.")
    bridge_window_seconds = _require_number(plan, "bridge_window_seconds")
    if not 0 <= bridge_window_seconds <= 10:
        raise ValueError("Plan bridge_window_seconds must be from 0.0 to 10.0.")
    lookahead_mode = plan.get("cropped_lookahead_conditioning")
    if lookahead_mode not in ("audio_only", "future_image"):
        raise ValueError("Plan cropped_lookahead_conditioning is invalid.")

    transition_frames = plan.get("transition_frames")
    if not isinstance(transition_frames, list) or any(
        not isinstance(frame, int) or isinstance(frame, bool)
        for frame in transition_frames
    ):
        raise TypeError("Plan transition_frames must be a list of integers.")
    if len(transition_frames) != image_count - 1:
        raise ValueError("Plan transition_frames do not match image_count.")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Plan tasks must be a non-empty list.")

    expected_output_start = 0
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise TypeError(f"Plan task {task_index} is not a dictionary.")
        if _require_int(task, "task_index") != task_index:
            raise ValueError(f"Plan task {task_index} has a mismatched task_index.")

        prompt_image_index = _require_int(task, "prompt_image_index")
        render_start = _require_int(task, "render_start_frame")
        render_frames = _require_int(task, "render_frames")
        output_start = _require_int(task, "output_start_frame")
        output_end = _require_int(task, "output_end_frame")
        crop_start = _require_int(task, "crop_start_frames")
        keep_frames = _require_int(task, "keep_frames")
        trailing_crop = _require_int(task, "trailing_crop_frames")
        audio_start = _require_int(task, "audio_start_sample")
        audio_end = _require_int(task, "audio_end_sample")
        audio_pad_start = _require_int(task, "audio_pad_start_samples")
        audio_pad_end = _require_int(task, "audio_pad_end_samples")
        output_start_sample = _require_int(task, "output_start_sample")
        output_end_sample = _require_int(task, "output_end_sample")
        visible_image_index = _optional_int(
            task, "visible_transition_image_index"
        )
        visible_frame_index = _optional_int(
            task, "visible_transition_frame_index"
        )
        visible_output_frame = _optional_int(
            task, "visible_transition_output_frame"
        )
        bridge_endpoint_image_index = _optional_int(
            task, "bridge_endpoint_image_index"
        )
        bridge_endpoint_frame_index = _optional_int(
            task, "bridge_endpoint_frame_index"
        )
        bridge_endpoint_output_frame = _optional_int(
            task, "bridge_endpoint_output_frame"
        )
        lookahead_image_index = _optional_int(task, "lookahead_image_index")
        lookahead_frame_index = _optional_int(task, "lookahead_frame_index")
        lookahead_output_frame = _optional_int(task, "lookahead_output_frame")
        has_continuity = _require_bool(task, "has_continuity")
        is_destination_handoff = _require_bool(task, "is_destination_handoff")
        starts_hard_cut = _require_bool(task, "starts_hard_cut")

        if prompt_image_index < 0 or prompt_image_index >= image_count:
            raise ValueError(
                f"Plan task {task_index} has an invalid prompt image index."
            )
        if output_start != expected_output_start or output_end <= output_start:
            raise ValueError(f"Plan task {task_index} is not timeline-contiguous.")
        if keep_frames != output_end - output_start:
            raise ValueError(f"Plan task {task_index} has an invalid retained range.")
        if not 124 <= render_frames <= 362 or render_frames % 17 != 5:
            raise ValueError(f"Plan task {task_index} has an invalid H3 render length.")
        if is_destination_handoff:
            expected_crop = _DESTINATION_CROP_FRAMES
        elif task_index == 0 or starts_hard_cut:
            expected_crop = 0
        else:
            expected_crop = overlap_frames
        if crop_start != expected_crop or render_start != output_start - crop_start:
            raise ValueError(f"Plan task {task_index} has invalid overlap metadata.")
        if has_continuity != (crop_start == overlap_frames):
            raise ValueError(f"Plan task {task_index} has invalid continuity metadata.")
        if is_destination_handoff and has_continuity:
            raise ValueError(
                f"Plan task {task_index} destination handoff cannot use continuity."
            )
        if starts_hard_cut and task_index == 0:
            raise ValueError("Plan task 0 cannot be marked as a hard cut.")
        if trailing_crop != render_frames - crop_start - keep_frames:
            raise ValueError(f"Plan task {task_index} has invalid trailing crop metadata.")
        if trailing_crop < 0:
            raise ValueError(f"Plan task {task_index} exceeds its render length.")

        requested_audio_start = _frame_to_sample(
            render_start, conditioning_sample_rate
        )
        requested_audio_end = _frame_to_sample(
            render_start + render_frames, conditioning_sample_rate
        )
        expected_audio_start = max(
            0, min(conditioning_sample_count, requested_audio_start)
        )
        expected_audio_end = max(
            0, min(conditioning_sample_count, requested_audio_end)
        )
        if audio_start != expected_audio_start or audio_end != expected_audio_end:
            raise ValueError(f"Plan task {task_index} has invalid guide-audio samples.")
        if audio_pad_start != max(0, -requested_audio_start) or audio_pad_end != max(
            0, requested_audio_end - conditioning_sample_count
        ):
            raise ValueError(f"Plan task {task_index} has invalid audio padding.")
        if output_start_sample != min(
            sample_count, _frame_to_sample(output_start, sample_rate)
        ) or output_end_sample != min(
            sample_count, _frame_to_sample(output_end, sample_rate)
        ):
            raise ValueError(f"Plan task {task_index} has invalid output samples.")

        visible_values = (
            visible_image_index,
            visible_frame_index,
            visible_output_frame,
        )
        if any(value is None for value in visible_values) and not all(
            value is None for value in visible_values
        ):
            raise ValueError(
                f"Plan task {task_index} has incomplete visible-transition metadata."
            )
        if visible_image_index is not None:
            if not 0 <= visible_image_index < image_count:
                raise ValueError(
                    f"Plan task {task_index} has an invalid visible-transition image."
                )
            if is_destination_handoff:
                if (
                    visible_frame_index != _DESTINATION_ANCHOR_FRAME
                    or visible_frame_index >= crop_start
                    or visible_output_frame != output_start
                ):
                    raise ValueError(
                        f"Plan task {task_index} hidden destination anchor is "
                        "inconsistent."
                    )
            else:
                if not crop_start <= visible_frame_index < crop_start + keep_frames:
                    raise ValueError(
                        f"Plan task {task_index} visible anchor would be removed "
                        "by crop."
                    )
                if visible_output_frame != render_start + visible_frame_index:
                    raise ValueError(
                        f"Plan task {task_index} visible anchor is inconsistent."
                    )
        elif is_destination_handoff:
            raise ValueError(
                f"Plan task {task_index} destination handoff has no hidden anchor."
            )

        endpoint_values = (
            bridge_endpoint_image_index,
            bridge_endpoint_frame_index,
            bridge_endpoint_output_frame,
        )
        if any(value is None for value in endpoint_values) and not all(
            value is None for value in endpoint_values
        ):
            raise ValueError(
                f"Plan task {task_index} has incomplete bridge-endpoint metadata."
            )
        if bridge_endpoint_frame_index is not None:
            retained_end = crop_start + keep_frames
            if not 0 <= bridge_endpoint_image_index < image_count:
                raise ValueError(
                    f"Plan task {task_index} has an invalid bridge-endpoint image."
                )
            if visible_frame_index is not None:
                raise ValueError(
                    f"Plan task {task_index} cannot retain a visible anchor and own "
                    "a hidden bridge endpoint."
                )
            if bridge_endpoint_frame_index != retained_end:
                raise ValueError(
                    f"Plan task {task_index} bridge endpoint must be its first "
                    "discarded frame."
                )
            if bridge_endpoint_frame_index >= render_frames:
                raise ValueError(
                    f"Plan task {task_index} bridge endpoint is not rendered."
                )
            if bridge_endpoint_output_frame != (
                render_start + bridge_endpoint_frame_index
            ) or bridge_endpoint_output_frame != output_end:
                raise ValueError(
                    f"Plan task {task_index} bridge endpoint is inconsistent."
                )

        lookahead_values = (
            lookahead_image_index,
            lookahead_frame_index,
            lookahead_output_frame,
        )
        if any(value is None for value in lookahead_values) and not all(
            value is None for value in lookahead_values
        ):
            raise ValueError(
                f"Plan task {task_index} has incomplete lookahead metadata."
            )
        if lookahead_image_index is not None:
            if lookahead_mode != "future_image":
                raise ValueError(
                    f"Plan task {task_index} has a disabled lookahead anchor."
                )
            if not 0 <= lookahead_image_index < image_count:
                raise ValueError(
                    f"Plan task {task_index} has an invalid lookahead image."
                )
            retained_end = crop_start + keep_frames
            if not retained_end <= lookahead_frame_index < render_frames:
                raise ValueError(
                    f"Plan task {task_index} lookahead anchor is not fully cropped."
                )
            if lookahead_output_frame != render_start + lookahead_frame_index:
                raise ValueError(
                    f"Plan task {task_index} lookahead anchor is inconsistent."
                )

        if _require_bool(task, "is_final") != (output_end == total_frames):
            raise ValueError(f"Plan task {task_index} has an invalid final-task flag.")
        expected_output_start = output_end

    if expected_output_start != total_frames:
        raise ValueError("Plan tasks do not end at total_frames.")
    continuation_frames = plan.get("continuation_frames")
    if not isinstance(continuation_frames, list) or any(
        not isinstance(frame, int) or isinstance(frame, bool)
        for frame in continuation_frames
    ):
        raise TypeError("Plan continuation_frames must be a list of integers.")
    expected_continuations = (
        [_require_int(task, "output_end_frame") for task in tasks[:-1]]
        if image_count == 1
        else []
    )
    if continuation_frames != expected_continuations:
        raise ValueError("Plan continuation_frames do not match its task seams.")

    visible_anchors = [
        (
            _optional_int(task, "visible_transition_output_frame"),
            _optional_int(task, "visible_transition_image_index"),
        )
        for task in tasks
        if task.get("visible_transition_output_frame") is not None
    ]
    expected_visible = list(zip(transition_frames, range(1, image_count)))
    if visible_anchors != expected_visible:
        raise ValueError("Plan visible anchors do not match transition_frames.")

    endpoint_anchors = [
        (
            _optional_int(task, "bridge_endpoint_output_frame"),
            _optional_int(task, "bridge_endpoint_image_index"),
            task_index,
        )
        for task_index, task in enumerate(tasks)
        if task.get("bridge_endpoint_output_frame") is not None
    ]
    expects_bridge_endpoints = (
        plan.get("transition_mode") == "first_last_bridge"
        and bridge_span == "short_window"
        and bridge_window_seconds > 0
    )
    expected_endpoints = (
        [
            (frame, image_index, None)
            for frame, image_index in expected_visible
        ]
        if expects_bridge_endpoints
        else []
    )
    if [(frame, image, None) for frame, image, _index in endpoint_anchors] != (
        expected_endpoints
    ):
        raise ValueError("Plan bridge endpoints do not match transition_frames.")
    for endpoint_output, endpoint_image, task_index in endpoint_anchors:
        task = tasks[task_index]
        if _require_int(task, "prompt_image_index") != endpoint_image - 1:
            raise ValueError(
                f"Plan task {task_index} bridge endpoint has a mismatched source image."
            )
        if task_index + 1 >= len(tasks):
            raise ValueError(
                f"Plan task {task_index} bridge endpoint has no following anchor task."
            )
        following = tasks[task_index + 1]
        if (
            _require_int(following, "output_start_frame") != endpoint_output
            or _require_bool(following, "has_continuity")
            or _require_bool(following, "starts_hard_cut")
            or not _require_bool(following, "is_destination_handoff")
            or _require_int(following, "crop_start_frames")
            != _DESTINATION_CROP_FRAMES
            or _require_int(following, "prompt_image_index") != endpoint_image
            or _optional_int(following, "visible_transition_output_frame")
            != endpoint_output
            or _optional_int(following, "visible_transition_image_index")
            != endpoint_image
            or _optional_int(following, "visible_transition_frame_index")
            != _DESTINATION_ANCHOR_FRAME
        ):
            raise ValueError(
                f"Plan task {task_index} bridge endpoint is not paired with the "
                "following hidden-anchor destination task."
            )
    return plan, tasks


class RvVideo_MiniMaxH3AudioPlanStep(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMax H3 Audio Plan Step [Eclipse]",
            display_name="MiniMax H3 Audio Plan Step",
            description=(
                "Resolves one H3 task into a frame-exact conditioning-audio "
                "slice, ordinary generated continuity, hidden image anchors, "
                "render length, and crop metadata."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[
                io.Custom(_PLAN_KIND).Input(
                    "plan",
                    tooltip="Version-4 plan from the H3 Audio Timeline Planner.",
                ),
                io.Int.Input(
                    "task_index",
                    default=0,
                    min=0,
                    max=65535,
                    step=1,
                    tooltip="Zero-based task number resolved by this step.",
                ),
                io.Audio.Input(
                    "master_audio",
                    tooltip=(
                        "Original master audio used to verify the plan. It remains "
                        "authoritative for timeline duration and final muxing."
                    ),
                ),
                io.Image.Input(
                    "image_batch",
                    tooltip="Timeline reference images in the planner's exact order.",
                ),
                io.Image.Input(
                    "previous_frames",
                    optional=True,
                    tooltip=(
                        "Accumulated retained output before this task. Ordinary "
                        "extensions use its final 22 frames; destination handoffs "
                        "validate it but do not condition on the old image."
                    ),
                ),
                io.Audio.Input(
                    "conditioning_audio",
                    optional=True,
                    tooltip=(
                        "Optional H3 guide audio, typically raw Demucs vocals. "
                        "Slices preserve its native sample rate. When disconnected, "
                        "the master audio is used."
                    ),
                ),
            ],
            outputs=[
                io.Audio.Output("audio_slice"),
                io.Image.Output("continuity_clip"),
                io.Boolean.Output("has_continuity"),
                io.Image.Output("prompt_image"),
                io.Image.Output("visible_transition_image"),
                io.Boolean.Output("has_visible_transition"),
                io.Int.Output("visible_transition_frame_index"),
                io.Image.Output("lookahead_image"),
                io.Boolean.Output("has_lookahead"),
                io.Int.Output("lookahead_frame_index"),
                io.Int.Output("render_frames"),
                io.Int.Output("crop_start_frames"),
                io.Int.Output("keep_frames"),
                io.Boolean.Output("has_bridge_endpoint"),
                io.Int.Output("bridge_endpoint_frame_index"),
            ],
        )

    @classmethod
    def execute(
        cls,
        plan,
        task_index,
        master_audio,
        image_batch,
        previous_frames=None,
        conditioning_audio=None,
    ):
        plan_value, tasks = _validate_plan(unwrap_value(plan))
        task_index = unwrap_value(task_index, 0)
        if not isinstance(task_index, int) or isinstance(task_index, bool):
            raise TypeError("task_index must be an integer.")
        if task_index < 0 or task_index >= len(tasks):
            raise ValueError(
                f"task_index {task_index} is outside the plan's {len(tasks)} tasks."
            )

        if not isinstance(master_audio, dict):
            raise TypeError("master_audio must be a ComfyUI AUDIO value.")
        waveform = master_audio.get("waveform")
        sample_rate = master_audio.get("sample_rate")
        if not isinstance(waveform, torch.Tensor) or waveform.ndim < 1:
            raise ValueError("master_audio waveform is missing or invalid.")
        if sample_rate != _require_int(plan_value, "sample_rate"):
            raise ValueError("master_audio sample rate differs from the plan.")
        if waveform.shape[-1] != _require_int(plan_value, "sample_count"):
            raise ValueError("master_audio sample count differs from the plan.")

        guide_audio = master_audio if conditioning_audio is None else conditioning_audio
        if not isinstance(guide_audio, dict):
            raise TypeError("conditioning_audio must be a ComfyUI AUDIO value.")
        guide_waveform = guide_audio.get("waveform")
        guide_sample_rate = guide_audio.get("sample_rate")
        if not isinstance(guide_waveform, torch.Tensor) or guide_waveform.ndim < 1:
            raise ValueError("conditioning_audio waveform is missing or invalid.")
        if guide_sample_rate != _require_int(
            plan_value, "conditioning_sample_rate"
        ):
            raise ValueError("conditioning_audio sample rate differs from the plan.")
        if guide_waveform.shape[-1] != _require_int(
            plan_value, "conditioning_sample_count"
        ):
            raise ValueError("conditioning_audio sample count differs from the plan.")

        images = flatten_images(image_batch)
        image_count = _require_int(plan_value, "image_count")
        if len(images) != image_count:
            raise ValueError(
                f"image_batch contains {len(images)} images but the plan expects "
                f"{image_count}."
            )

        task = tasks[task_index]
        output_start = _require_int(task, "output_start_frame")
        overlap_frames = _require_int(plan_value, "overlap_frames")
        has_continuity = _require_bool(task, "has_continuity")
        if task_index == 0:
            if previous_frames is not None:
                raise ValueError("Task 0 must not receive previous_frames.")
        else:
            if not isinstance(previous_frames, torch.Tensor) or previous_frames.ndim != 4:
                raise ValueError("Extension tasks require one 4D previous_frames batch.")
            if previous_frames.shape[0] != output_start:
                raise ValueError(
                    f"Accumulated timeline drift at task {task_index}: expected "
                    f"{output_start} frames, received {previous_frames.shape[0]}."
                )
            if has_continuity and previous_frames.shape[0] < overlap_frames:
                raise ValueError(
                    "The accumulated timeline does not contain 22 continuity frames."
                )
        prompt_image_index = _require_int(task, "prompt_image_index")
        prompt_image = images[prompt_image_index]

        visible_image_index = _optional_int(
            task, "visible_transition_image_index"
        )
        bridge_endpoint_image_index = _optional_int(
            task, "bridge_endpoint_image_index"
        )
        guide_image_index = (
            visible_image_index
            if visible_image_index is not None
            else bridge_endpoint_image_index
        )
        visible_image = (
            None if guide_image_index is None else images[guide_image_index]
        )
        has_visible_transition = visible_image_index is not None
        visible_frame_index = _optional_int(
            task, "visible_transition_frame_index"
        )
        bridge_endpoint_frame_index = _optional_int(
            task, "bridge_endpoint_frame_index"
        )
        has_bridge_endpoint = bridge_endpoint_frame_index is not None

        # Destination handoffs deliberately omit the preceding identity. Their
        # first 22 frames are generated from destination prompt tokens and guide
        # audio, then an exact destination anchor is rendered at local frame 22.
        # All 23 frames are cropped, so local frame 23 is the first visible frame.
        continuity_clip = (
            previous_frames[-overlap_frames:] if has_continuity else None
        )

        lookahead_image_index = _optional_int(task, "lookahead_image_index")
        lookahead_image = (
            None
            if lookahead_image_index is None
            else images[lookahead_image_index]
        )
        has_lookahead = lookahead_image is not None
        lookahead_frame_index = _optional_int(task, "lookahead_frame_index")

        audio_start = _require_int(task, "audio_start_sample")
        audio_end = _require_int(task, "audio_end_sample")
        audio_slice = dict(guide_audio)
        audio_waveform = guide_waveform[..., audio_start:audio_end].clone()
        audio_pad_start = _require_int(task, "audio_pad_start_samples")
        audio_pad_end = _require_int(task, "audio_pad_end_samples")
        if audio_pad_start or audio_pad_end:
            audio_waveform = torch.nn.functional.pad(
                audio_waveform, (audio_pad_start, audio_pad_end)
            )
        audio_slice["waveform"] = audio_waveform
        audio_slice["sample_rate"] = guide_sample_rate

        render_frames = _require_int(task, "render_frames")
        crop_start = _require_int(task, "crop_start_frames")
        keep_frames = _require_int(task, "keep_frames")
        log.debug(
            _LOG_PREFIX,
            f"Task {task_index}: audio [{audio_start},{audio_end}), render "
            f"{render_frames}, crop {crop_start}, keep {keep_frames}, visible "
            f"{visible_frame_index if has_visible_transition else 'none'}, "
            f"endpoint {bridge_endpoint_frame_index if has_bridge_endpoint else 'none'}, "
            f"lookahead {lookahead_frame_index if has_lookahead else 'none'}, "
            f"continuity {'generated' if has_continuity else 'none'}; "
            f"guide rate {guide_sample_rate} Hz.",
        )
        return io.NodeOutput(
            audio_slice,
            continuity_clip,
            has_continuity,
            prompt_image,
            visible_image,
            has_visible_transition,
            visible_frame_index if visible_frame_index is not None else -1,
            lookahead_image,
            has_lookahead,
            lookahead_frame_index if lookahead_frame_index is not None else -1,
            render_frames,
            crop_start,
            keep_frames,
            has_bridge_endpoint,
            (
                bridge_endpoint_frame_index
                if bridge_endpoint_frame_index is not None
                else -1
            ),
        )
