# MiniMax H3 Segmented Conditioning V2 [Eclipse]
#
# Builds either native-style FL2VA positional conditioning or one persistent
# Ref2VA active-image reference. The families are deliberately exclusive.

import math
from typing import Any

import comfy.utils  # type: ignore
import node_helpers  # type: ignore
import torch  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.image_helpers import flatten_images
from ..core.minimax_h3_segment_plan import (
    CONDITIONING_FAMILIES,
    PLAN_KIND,
    REF_IMAGE_SIZES,
    task_uses_intentional_camera_cut,
    validate_segment_plan,
)

_CANVAS_MULTIPLE = 32
_REF_IMAGE_SHORT_EDGE = 2048


def _single(value: Any, default: Any = None) -> Any:
    while isinstance(value, (list, tuple)):
        if not value:
            return default
        value = value[0]
    return default if value is None else value


def _prompt_items(value: Any) -> list[str]:
    prompts: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            for child in item:
                collect(child)
        elif isinstance(item, str):
            prompts.extend(line.strip() for line in item.splitlines() if line.strip())

    collect(value)
    return prompts or [""]


def _one_image(value: Any, name: str) -> torch.Tensor:
    images = flatten_images(value)
    if not images:
        raise ValueError(f"{name} must contain one IMAGE.")
    return images[0]


def _resize(image: torch.Tensor, width: int, height: int, crop: str) -> torch.Tensor:
    frame = image[:1, ..., :3].movedim(-1, 1)
    return comfy.utils.common_upscale(
        frame, width, height, "lanczos", crop
    ).movedim(1, -1)


def _reference_geometry(
    image: torch.Tensor,
    width: int,
    height: int,
    ref_image_size: str,
) -> tuple[int, int]:
    source_height, source_width = image.shape[1:3]
    if source_height < 1 or source_width < 1:
        raise ValueError("source_image has invalid dimensions.")
    if ref_image_size == "match":
        scale = min(
            1.0,
            math.sqrt((width * height) / (source_width * source_height)),
        )
    else:
        scale = min(1.0, _REF_IMAGE_SHORT_EDGE / min(source_width, source_height))
    target_width = max(
        _CANVAS_MULTIPLE,
        round(source_width * scale / _CANVAS_MULTIPLE) * _CANVAS_MULTIPLE,
    )
    target_height = max(
        _CANVAS_MULTIPLE,
        round(source_height * scale / _CANVAS_MULTIPLE) * _CANVAS_MULTIPLE,
    )
    return target_width, target_height


class RvVideo_MiniMaxH3SegmentedConditioningV2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MiniMax H3 Segmented Conditioning V2 [Eclipse]",
            display_name="MiniMax H3 Segmented Conditioning V2",
            description=(
                "Selects one complete task prompt, falling back to the first "
                "non-empty prompt, then builds exclusive FL2VA keyframes or one "
                "persistent Ref2VA Picture 1 reference."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            is_input_list=True,
            inputs=[
                io.Clip.Input("clip", tooltip="Matching H3 text/vision encoder."),
                io.Vae.Input("vae", tooltip="Matching H3 VisualVAE."),
                io.String.Input(
                    "prompts",
                    force_input=True,
                    tooltip=(
                        "Connect String Multiline List's string_list output. "
                        "Non-empty lines map to images; an unavailable index "
                        "falls back to line 0, so one line applies everywhere."
                    ),
                ),
                io.Int.Input(
                    "prompt_index",
                    default=0,
                    min=0,
                    max=65535,
                    step=1,
                    tooltip="Task prompt owner from V2 Plan Step.",
                ),
                io.String.Input(
                    "conditioning_family",
                    default="fl2va_keyframes",
                    tooltip=(
                        "Must match the loaded checkpoint and the V2 plan. The "
                        "node never mixes minimax_keyframes and minimax_refs."
                    ),
                ),
                io.Image.Input(
                    "source_image",
                    tooltip="Active original timeline image for this task.",
                ),
                io.Boolean.Input(
                    "has_start_guide",
                    default=True,
                    tooltip="Install source_image as a positional FL2VA first frame.",
                ),
                io.Int.Input(
                    "start_guide_frame_index",
                    default=0,
                    min=-1,
                    max=4096,
                    step=1,
                    tooltip="Local source keyframe index; -1 means absent.",
                ),
                io.Image.Input(
                    "last_image",
                    optional=True,
                    tooltip=(
                        "FL2VA bridge destination or experimental same-source "
                        "hidden endpoint. Ignored when has_last_image is false."
                    ),
                ),
                io.Boolean.Input(
                    "has_last_image",
                    default=False,
                    tooltip="Install last_image as a positional FL2VA endpoint.",
                ),
                io.Int.Input(
                    "endpoint_frame_index",
                    default=-1,
                    min=-1,
                    max=4096,
                    step=1,
                    tooltip="Local hidden endpoint index; -1 means absent.",
                ),
                io.Int.Input(
                    "width",
                    default=1344,
                    min=32,
                    max=16384,
                    step=32,
                    tooltip="Generation canvas width.",
                ),
                io.Int.Input(
                    "height",
                    default=768,
                    min=32,
                    max=16384,
                    step=32,
                    tooltip="Generation canvas height.",
                ),
                io.String.Input(
                    "ref_image_size",
                    default="match",
                    tooltip=(
                        "Ref2VA geometry: match limits toward canvas area; max "
                        "allows a 2048-pixel short edge. Neither mode upscales."
                    ),
                ),
                io.Custom(PLAN_KIND).Input(
                    "segment_plan",
                    optional=True,
                    tooltip=(
                        "Optional V2 plan context. Connect with task_index to apply "
                        "intentional camera-cut text only at eligible technical resets."
                    ),
                ),
                io.Int.Input(
                    "task_index",
                    optional=True,
                    force_input=True,
                    min=0,
                    max=65535,
                    step=1,
                    tooltip="Current V2 plan task index; requires segment_plan.",
                ),
            ],
            outputs=[
                io.Conditioning.Output(
                    "positive",
                    tooltip=(
                        "H3 conditioning containing either minimax_keyframes or "
                        "minimax_refs, never both."
                    ),
                )
            ],
        )

    @classmethod
    def execute(
        cls,
        clip,
        vae,
        prompts,
        prompt_index,
        conditioning_family,
        source_image,
        has_start_guide,
        start_guide_frame_index,
        has_last_image,
        endpoint_frame_index,
        width,
        height,
        ref_image_size,
        last_image=None,
        segment_plan=None,
        task_index=None,
    ):
        clip = _single(clip)
        vae = _single(vae)
        prompt_index = _single(prompt_index, 0)
        conditioning_family = _single(
            conditioning_family, "fl2va_keyframes"
        )
        has_start_guide = _single(has_start_guide, True)
        start_guide_frame_index = _single(start_guide_frame_index, 0)
        has_last_image = _single(has_last_image, False)
        endpoint_frame_index = _single(endpoint_frame_index, -1)
        width = _single(width, 1344)
        height = _single(height, 768)
        ref_image_size = _single(ref_image_size, "match")
        segment_plan = _single(segment_plan)
        task_index = _single(task_index)
        source = _one_image(source_image, "source_image")

        if clip is None or vae is None:
            raise ValueError("clip and vae are required.")
        if not isinstance(prompt_index, int) or isinstance(prompt_index, bool):
            raise TypeError("prompt_index must be an integer.")
        if conditioning_family not in CONDITIONING_FAMILIES:
            raise ValueError(f"Unsupported conditioning_family: {conditioning_family}")
        if ref_image_size not in REF_IMAGE_SIZES:
            raise ValueError(f"Unsupported ref_image_size: {ref_image_size}")
        if not isinstance(has_start_guide, bool) or not isinstance(
            has_last_image, bool
        ):
            raise TypeError("Guide flags must be boolean.")
        if (
            not isinstance(start_guide_frame_index, int)
            or isinstance(start_guide_frame_index, bool)
            or not isinstance(endpoint_frame_index, int)
            or isinstance(endpoint_frame_index, bool)
        ):
            raise TypeError("Guide frame indices must be integers.")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or width < 32
            or height < 32
        ):
            raise ValueError("width and height must be positive integer canvases.")

        prompt_lines = _prompt_items(prompts)
        selected_prompt = (
            prompt_lines[prompt_index]
            if 0 <= prompt_index < len(prompt_lines)
            else prompt_lines[0]
        )
        if segment_plan is None:
            if task_index is not None:
                raise ValueError("task_index requires segment_plan.")
        else:
            plan, tasks = validate_segment_plan(segment_plan)
            if not isinstance(task_index, int) or isinstance(task_index, bool):
                raise TypeError(
                    "task_index must be an integer when segment_plan is connected."
                )
            if not 0 <= task_index < len(tasks):
                raise ValueError(
                    f"task_index {task_index} is outside the plan's {len(tasks)} tasks."
                )
            task = tasks[task_index]
            if task["conditioning_family"] != conditioning_family:
                raise ValueError(
                    "conditioning_family differs from the connected segment_plan task."
                )
            if task_uses_intentional_camera_cut(plan, task):
                selected_prompt = (
                    f"{selected_prompt.rstrip()}\n"
                    f"{plan['technical_cut_instruction'].strip()}"
                ).strip()

        if conditioning_family == "ref2va_active_reference":
            if has_start_guide or has_last_image:
                raise ValueError(
                    "Ref2VA cannot receive positional start or endpoint guides."
                )
            target_width, target_height = _reference_geometry(
                source, width, height, ref_image_size
            )
            resized = _resize(source, target_width, target_height, "disabled")
            prompt = selected_prompt
            if "<picture 1>" not in prompt.casefold():
                prompt = (
                    "<Picture 1> is the active subject, appearance, and scene "
                    f"reference. {prompt}"
                ).strip()
            ref_items = [{"type": "image", "data": resized}]
            tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)
            conditioning = clip.encode_from_tokens_scheduled(tokens)
            latent = vae.encode(resized)
            ref_blocks = [
                {
                    "kind": "image",
                    "latent_h": target_height // 16,
                    "latent_w": target_width // 16,
                    "latent": latent,
                }
            ]
            conditioning = node_helpers.conditioning_set_values(
                conditioning, {"minimax_refs": ref_blocks}
            )
            return io.NodeOutput(conditioning)

        endpoint = None
        if has_last_image:
            endpoint = _one_image(last_image, "last_image")
            if endpoint_frame_index < 0:
                raise ValueError("FL2VA endpoint index must be non-negative.")
        elif endpoint_frame_index != -1:
            raise ValueError("endpoint_frame_index must be -1 when no endpoint exists.")
        if has_start_guide and start_guide_frame_index < 0:
            raise ValueError("FL2VA start-guide index must be non-negative.")
        if not has_start_guide and start_guide_frame_index != -1:
            raise ValueError(
                "start_guide_frame_index must be -1 when no start guide exists."
            )

        first = _resize(source, width, height, "disabled")
        token_images = [first]
        keyframes = []
        if has_start_guide:
            keyframes.append(
                {
                    "resolved_frame_index": start_guide_frame_index,
                    "latent": vae.encode(first),
                }
            )
        if endpoint is not None:
            last = _resize(endpoint, width, height, "center")
            token_images.append(last)
            keyframes.append(
                {
                    "resolved_frame_index": endpoint_frame_index,
                    "latent": vae.encode(last),
                }
            )
        tokens = clip.tokenize(selected_prompt, images=token_images)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        if keyframes:
            conditioning = node_helpers.conditioning_set_values(
                conditioning, {"minimax_keyframes": keyframes}
            )
        return io.NodeOutput(conditioning)
