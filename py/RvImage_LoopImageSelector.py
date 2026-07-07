#
# Loop Image Selector [Eclipse]
#
# Dynamically selects the reference image for a video loop based on loop index
# and automatically concatenates or blends context frames at transition points.
#

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "LoopImageSelector"


def _pyramid_blend(src: torch.Tensor, dst: torch.Tensor, alpha: torch.Tensor, levels: int = 4) -> torch.Tensor:
    # Multi-scale Laplacian pyramid blend.
    # src, dst: [N, H, W, C]   alpha: [N, 1, 1, 1] in 0..1
    # Each spatial frequency band is blended independently with the same per-frame alpha,
    # which produces sharper edges than a plain pixel-level crossfade.
    src_f = src.float().permute(0, 3, 1, 2)  # NCHW
    dst_f = dst.float().permute(0, 3, 1, 2)

    def _laplacian(x: torch.Tensor) -> list:
        gauss = [x]
        for _ in range(levels - 1):
            gauss.append(F.avg_pool2d(gauss[-1], kernel_size=2, stride=2, padding=0))
        lap = []
        for i in range(levels - 1):
            up = F.interpolate(gauss[i + 1], size=gauss[i].shape[2:], mode="bilinear", align_corners=False)
            lap.append(gauss[i] - up)
        lap.append(gauss[-1])  # coarsest level stored as-is
        return lap

    lap_src = _laplacian(src_f)
    lap_dst = _laplacian(dst_f)

    blended_lap = [(1 - alpha) * ls + alpha * ld for ls, ld in zip(lap_src, lap_dst)]

    result = blended_lap[-1]
    for lap in reversed(blended_lap[:-1]):
        result = F.interpolate(result, size=lap.shape[2:], mode="bilinear", align_corners=False) + lap

    return result.permute(0, 2, 3, 1).to(src.dtype)  # back to NHWC


def _resize_preserve_aspect(tensor: torch.Tensor, target_h: int, target_w: int) -> torch.Tensor:
    # tensor: [B, H, W, C]
    # returns: [B, target_h, target_w, C]
    B, H, W, C = tensor.shape
    target_aspect = target_w / target_h
    input_aspect = W / H

    if input_aspect > target_aspect:
        # crop horizontally
        crop_w = max(1, round(H * target_aspect))
        left = (W - crop_w) // 2
        tensor = tensor[:, :, left : left + crop_w, :]
    elif input_aspect < target_aspect:
        # crop vertically
        crop_h = max(1, round(W / target_aspect))
        top = (H - crop_h) // 2
        tensor = tensor[:, top : top + crop_h, :, :]

    # Now interpolate to target size
    tensor_NCHW = tensor.permute(0, 3, 1, 2).float()
    resized_NCHW = F.interpolate(tensor_NCHW, size=(target_h, target_w), mode="bilinear", align_corners=False)
    return resized_NCHW.permute(0, 2, 3, 1).to(tensor.dtype)


class RvImage_LoopImageSelector(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Loop Image Selector [Eclipse]",
            display_name="Loop Image Selector",
            description=(
                "Dynamically selects reference images at specified loop indices,\n"
                "and automatically combines them with loop context frames at transitions.\n"
                "Supports both dynamically expanding slots (image_1, image_2, ...) and image batches."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            inputs=[
                io.Int.Input(
                    "loop_index",
                    default=0,
                    min=0,
                    max=4096,
                    step=1,
                    tooltip="The current loop index (e.g. from Get_loop_index).",
                ),
                io.String.Input(
                    "loop_positions",
                    default="0, 2",
                    tooltip="Comma-separated list of loop indices where transitions/image changes occur.",
                ),
                io.Int.Input(
                    "overlap",
                    default=9,
                    min=0,
                    max=4096,
                    step=1,
                    tooltip="Number of overlapping frames to blend or duplicate at transition points.",
                ),
                io.Combo.Input(
                    "overlap_mode",
                    options=["concat", "linear_blend", "pyramid_blend"],
                    default="concat",
                    tooltip="Mode to combine previous frames with the new image context.",
                ),
                io.Image.Input(
                    "image_batch",
                    optional=True,
                    tooltip="Optional batch of input images (alternative to individual connections).",
                ),
                io.Image.Input(
                    "previous_frames",
                    optional=True,
                    tooltip="The accumulated output frames of all previous loops (from the parent loop feedback).",
                ),
                io.Int.Input(
                    "inputcount",
                    default=2,
                    min=2,
                    max=64,
                    step=1,
                    socketless=True,
                    tooltip="Used by the dynamic inputs frontend to show connected image slots.",
                ),
            ],
            outputs=[
                io.Image.Output("ref_image", tooltip="The single selected reference image (length 1)."),
                io.Image.Output("context_frames", tooltip="The context frames to feed the sampler."),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.dynprompt],
        )

    @classmethod
    def execute(cls, loop_index, loop_positions, overlap, overlap_mode, image_batch=None, previous_frames=None, inputcount=2, **kwargs):
        positions = []
        if loop_positions:
            for x in loop_positions.split(","):
                x = x.strip()
                if x.isdigit():
                    positions.append(int(x))
        positions = sorted(list(set([0] + positions)))

        # 2. Find selected index (largest position - 1 <= loop_index for 0-based loop_index)
        idx = 0
        for i, pos in enumerate(positions):
            if loop_index >= pos - 1:
                idx = i

        # 3. Retrieve selected reference image (check dynamic inputs first, then batch)
        selected_image = None
        slot_name = f"image_{idx + 1}"
        if slot_name in kwargs and kwargs[slot_name] is not None:
            selected_image = kwargs[slot_name]
            log.debug(_LOG_PREFIX, f"Selected dynamic slot '{slot_name}' for loop_index {loop_index}")
        elif image_batch is not None:
            batch_idx = min(idx, image_batch.shape[0] - 1)
            selected_image = image_batch[batch_idx : batch_idx + 1]
            log.debug(_LOG_PREFIX, f"Selected batch frame {batch_idx} for loop_index {loop_index}")

        # Fallback to first available dynamic slot if selected is empty
        if selected_image is None:
            for i in range(1, 65):
                k = f"image_{i}"
                if kwargs.get(k) is not None:
                    selected_image = kwargs[k]
                    log.debug(_LOG_PREFIX, f"Fallback: Selected dynamic slot '{k}'")
                    break

        # Fallback to first batch frame if still empty
        if selected_image is None and image_batch is not None:
            selected_image = image_batch[0:1]
            log.debug(_LOG_PREFIX, "Fallback: Selected first batch frame")

        if selected_image is None:
            raise ValueError(
                f"LoopImageSelector: No reference image found for loop index {loop_index}. "
                "Please connect dynamic input slots (image_1, image_2, ...) or image_batch."
            )

        # Ensure the reference image has length 1
        ref_image = selected_image[:1]

        # 4. Context frames logic
        if previous_frames is None:
            # Base sampler or first execution, no previous context available
            context_frames = ref_image
        else:
            is_transition = (loop_index + 1 in positions) and (loop_index >= 0)
            if is_transition:
                # Loop transition: Combine previous frames with the new image context
                log.msg(
                    _LOG_PREFIX,
                    f"Transition detected at loop_index {loop_index}. Combining context via {overlap_mode} (overlap={overlap})."
                )
                
                # Duplicate the single frame of the new image to overlap length
                if overlap > 0:
                    duplicated = ref_image.repeat(overlap, 1, 1, 1)
                else:
                    duplicated = ref_image

                # Ensure spatial dimensions match previous context while preserving aspect ratio
                if previous_frames.shape[1:3] != duplicated.shape[1:3]:
                    H, W = previous_frames.shape[1], previous_frames.shape[2]
                    log.warning(
                        _LOG_PREFIX,
                        f"Resizing transition context frames from {duplicated.shape[1]}x{duplicated.shape[2]} "
                        f"to match previous context size {H}x{W} (preserving aspect ratio via center crop)."
                    )
                    duplicated = _resize_preserve_aspect(duplicated, H, W)

                if overlap_mode == "concat" or overlap == 0:
                    # Directly append without dropping any frames
                    context_frames = torch.cat((previous_frames, duplicated), dim=0)
                else:
                    # Blending modes: blend the overlap window between previous and new
                    actual_overlap = min(overlap, len(previous_frames), len(duplicated))
                    if actual_overlap > 0:
                        prefix = previous_frames[:-actual_overlap]
                        blend_src = previous_frames[-actual_overlap:]
                        blend_dst = duplicated[:actual_overlap]
                        suffix = duplicated[actual_overlap:]

                        alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
                        alpha = alpha.view(-1, 1, 1, 1)

                        if overlap_mode == "pyramid_blend":
                            blended = _pyramid_blend(blend_src, blend_dst, alpha)
                        else:  # linear_blend
                            blended = (1 - alpha) * blend_src + alpha * blend_dst

                        context_frames = torch.cat((prefix, blended, suffix), dim=0)
                    else:
                        context_frames = torch.cat((previous_frames, duplicated), dim=0)
            else:
                # Normal execution: simply pass previous context through unmodified
                context_frames = previous_frames

        return io.NodeOutput(ref_image, context_frames)
