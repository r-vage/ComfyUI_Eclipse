#
# Image Batch Extend With Overlap — blends two image batches at a shared
# overlap region for video generation extension.
#
# When both inputs are connected the overlapping frames are blended and the
# full extended sequence is returned.
# When only one input is connected that batch is returned as-is.
#
# Originally inspired by KJNodes ImageBatchExtendWithOverlap.
#

import math

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore

import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "ImageBatchExtendWithOverlap"

_OVERLAP_SIDE_OPTIONS = ["source", "new_images", "both"]
_OVERLAP_MODE_OPTIONS = ["linear_blend", "ease_in_out", "filmic_crossfade", "perceptual_crossfade", "average", "dissolve", "pyramid_blend", "clock_wipe", "clock_wipe_ccw", "wipe_left", "wipe_right", "wipe_top", "wipe_bottom", "cut", "concat", "match_ncc", "match_ncc+linear", "match_ncc+pyramid", "match_mse", "match_mse+linear", "match_mse+pyramid", "match_luminance_mse", "match_luminance_mse+linear", "match_luminance_mse+pyramid", "match_gradient_mse", "match_gradient_mse+linear", "match_gradient_mse+pyramid"]

# Longest-side pixel size used when downsampling frames before cross-batch comparison.
_MATCH_DOWNSAMPLE_SIZE = 512


def _find_cross_batch_match(source: torch.Tensor, new_images: torch.Tensor,
                             search_frames: int, metric: str = "ncc",
                             side: str = "both") -> tuple:
    # Find the (src_idx, new_idx) pair that minimises visual distance between
    # source[src_idx] and new_images[new_idx].
    #
    # side controls which search windows are used:
    #   both       — scan last N of source × first N of new_images; best pair across both windows
    #   source     — pin new_cut=0 (new_images[0] is the fixed reference); find the source tail
    #                frame that matches the start of new_images best
    #   new_images — pin src_cut to source[-1] (fixed reference); find the new_images head frame
    #                that matches the end of source best
    #
    # Frames are downsampled to _MATCH_DOWNSAMPLE_SIZE px before comparison so
    # the operation is fast even for large HD batches.
    #
    # Uses the same pairwise matrix approach as SaveVideo's _find_loop_pair:
    #   ||a-b||² = ||a||² + ||b||² − 2(a·b)   (avoids [S,N,px] memory)
    #
    # Metrics:
    #   ncc           — normalized cross-correlation (higher = better); invariant
    #                   to per-frame brightness scale and offset
    #   mse           — mean squared pixel error (lower = better)
    #   luminance_mse — BT.601 grayscale MSE; ignores hue/saturation differences
    #   gradient_mse  — edge-magnitude MSE; ignores color, matches structure only
    #
    # Returns:
    #     (src_absolute_idx: int, new_absolute_idx: int, score: float)
    n_src = source.shape[0]
    n_new = new_images.shape[0]
    search_n_src = max(1, min(search_frames, n_src))
    search_n_new = max(1, min(search_frames, n_new))

    if side == "source":
        # Pin new_images[0] as the fixed reference; scan only the source tail.
        src_start = max(0, n_src - search_n_src)
        new_end = 1
    elif side == "new_images":
        # Pin source[-1] as the fixed reference; scan only the new_images head.
        src_start = n_src - 1
        new_end = min(n_new, search_n_new)
    else:  # both
        src_start = max(0, n_src - search_n_src)
        new_end = min(n_new, search_n_new)

    h = min(source.shape[1], new_images.shape[1])
    w = min(source.shape[2], new_images.shape[2])
    scale = _MATCH_DOWNSAMPLE_SIZE / max(h, w)
    ds_h = max(1, round(h * scale))
    ds_w = max(1, round(w * scale))

    interp = torch.nn.functional.interpolate
    src_ds = interp(
        source[src_start:, ..., :3].permute(0, 3, 1, 2).float(),
        size=(ds_h, ds_w), mode="bilinear", align_corners=False,
    )  # [S, 3, ds_h, ds_w]
    new_ds = interp(
        new_images[:new_end, ..., :3].permute(0, 3, 1, 2).float(),
        size=(ds_h, ds_w), mode="bilinear", align_corners=False,
    )  # [N, 3, ds_h, ds_w]
    S, N = src_ds.shape[0], new_ds.shape[0]

    if metric == "ncc":
        s_flat = src_ds.reshape(S, -1)
        n_flat = new_ds.reshape(N, -1)
        s_norm = s_flat - s_flat.mean(dim=1, keepdim=True)
        n_norm = n_flat - n_flat.mean(dim=1, keepdim=True)
        # [S, N] pairwise NCC — higher = better
        sim = (s_norm @ n_norm.T) / (
            s_norm.norm(dim=1, keepdim=True) * n_norm.norm(dim=1).unsqueeze(0) + 1e-8
        )
        flat_best = int(sim.argmax().item())
        best_s, best_n = flat_best // N, flat_best % N
        score = float(sim[best_s, best_n].item())

    else:
        # MSE family: ||a-b||² = ||a||² + ||b||² − 2(a·b), lower = better
        if metric == "luminance_mse":
            luma_w = torch.tensor([0.299, 0.587, 0.114], device=src_ds.device).view(1, 3, 1, 1)
            s_feat = (src_ds * luma_w).sum(dim=1).reshape(S, -1)
            n_feat = (new_ds * luma_w).sum(dim=1).reshape(N, -1)
        elif metric == "gradient_mse":
            def _grad_mag(img: torch.Tensor) -> torch.Tensor:
                dx = img[:, :, :, 1:] - img[:, :, :, :-1]
                dy = img[:, :, 1:, :] - img[:, :, :-1, :]
                return (dx[:, :, :-1, :].pow(2) + dy[:, :, :, :-1].pow(2)).sqrt().mean(dim=1)
            s_feat = _grad_mag(src_ds).reshape(S, -1)
            n_feat = _grad_mag(new_ds).reshape(N, -1)
        else:  # mse
            s_feat = src_ds.reshape(S, -1)
            n_feat = new_ds.reshape(N, -1)
        Nf = s_feat.shape[1]
        s_sq = s_feat.pow(2).sum(dim=1) / Nf          # [S]
        n_sq = n_feat.pow(2).sum(dim=1) / Nf          # [N]
        cross = (s_feat @ n_feat.T) / Nf              # [S, N]
        scores = s_sq.unsqueeze(1) + n_sq.unsqueeze(0) - 2 * cross  # [S, N]
        flat_best = int(scores.argmin().item())
        best_s, best_n = flat_best // N, flat_best % N
        score = float(scores[best_s, best_n].item())

    return src_start + best_s, best_n, score


def _clock_wipe_mask(n: int, h: int, w: int, device: torch.device, dtype: torch.dtype, clockwise: bool = True) -> torch.Tensor:
    # Returns a boolean mask [N, H, W, 1] where True = use dst (new image).
    # For frame i the swept angle threshold goes from 0 to 2π exclusive,
    # sweeping clockwise (or counter-clockwise) from 12 o'clock.
    cy = (h - 1) / 2.0
    cx = (w - 1) / 2.0
    y = torch.arange(h, device=device, dtype=torch.float32)
    x = torch.arange(w, device=device, dtype=torch.float32)
    yy, xx = torch.meshgrid(y, x, indexing="ij")                  # [H, W]
    # atan2(dx, -dy): 0 at top, increases clockwise; result in (-π, π]
    angle = torch.atan2(xx - cx, cy - yy)
    angle = (angle + 2 * math.pi) % (2 * math.pi)                 # [H, W] in [0, 2π)
    if not clockwise:
        angle = (2 * math.pi - angle) % (2 * math.pi)             # flip direction
    # Per-frame threshold: frame 0 → tiny slice, frame N-1 → almost full circle
    thresholds = torch.linspace(0, 2 * math.pi, n + 2, device=device, dtype=torch.float32)[1:-1]  # [N]
    mask = angle.unsqueeze(0) < thresholds.view(-1, 1, 1)         # [N, H, W]
    return mask.unsqueeze(-1).to(dtype=torch.bool)                 # [N, H, W, 1]


def _linear_wipe_mask(n: int, h: int, w: int, device: torch.device, dtype: torch.dtype, direction: str = "left") -> torch.Tensor:
    # Returns a boolean mask [N, H, W, 1] where True = use dst (new image).
    # The mask sweeps a hard edge across the frame over N frames:
    #   left   — edge moves left→right (leftmost column first)
    #   right  — edge moves right→left (rightmost column first)
    #   top    — edge moves top→bottom (top row first)
    #   bottom — edge moves bottom→top (bottom row first)
    # Threshold goes from 0 to 1 exclusive via linspace; pixels whose normalised
    # coordinate is less than the threshold belong to dst.
    thresholds = torch.linspace(0, 1, n + 2, device=device, dtype=torch.float32)[1:-1]  # [N]
    if direction in ("left", "right"):
        coords = torch.arange(w, device=device, dtype=torch.float32) / w  # [W] in [0, 1)
        if direction == "right":
            coords = 1.0 - coords
        mask = coords.view(1, 1, w) < thresholds.view(n, 1, 1)            # [N, 1, W]
        mask = mask.expand(n, h, w)
    else:  # top / bottom
        coords = torch.arange(h, device=device, dtype=torch.float32) / h  # [H] in [0, 1)
        if direction == "bottom":
            coords = 1.0 - coords
        mask = coords.view(1, h, 1) < thresholds.view(n, 1, 1)            # [N, H, 1]
        mask = mask.expand(n, h, w)
    return mask.unsqueeze(-1).to(dtype=torch.bool)                         # [N, H, W, 1]


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


def _resize_to_match(images: torch.Tensor, target_h: int, target_w: int) -> torch.Tensor:
    # Scale to fill target size (preserving aspect ratio) then center-crop to exact dimensions.
    # Mirrors the "crop" fit mode in RvImage_Resize: scale = max(tw/W, th/H).
    h, w = images.shape[1], images.shape[2]
    if h == target_h and w == target_w:
        return images

    # Scale so both dimensions meet or exceed the target.
    scale = max(target_w / w, target_h / h)
    inter_w = max(round(w * scale), target_w)
    inter_h = max(round(h * scale), target_h)

    # Resize via comfy's upscaler (BHWC → BCHW → upscale → BHWC).
    samples = images.movedim(-1, 1)
    samples = comfy.utils.common_upscale(samples, inter_w, inter_h, "bilinear", "disabled")
    images = samples.movedim(1, -1)

    # Center-crop to exact target dimensions.
    cx = (inter_w - target_w) // 2
    cy = (inter_h - target_h) // 2
    return images[:, cy : cy + target_h, cx : cx + target_w, :]


class RvImage_BatchExtendWithOverlap(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Batch Extend With Overlap [Eclipse]",
            display_name="Image Batch Extend With Overlap",
            description=(
                "Extends a video batch by blending two image sequences at an overlapping region.\n"
                "Both inputs connected: blends the overlap and returns the full extended sequence.\n"
                "Only source_images: returned as-is.\n"
                "Only new_images: returned as-is.\n"
                "If the two inputs differ in size, new_images is center-cropped and resized to match source_images."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input(
                    "source_images",
                    optional=True,
                    tooltip="The source (first) image batch. Optional — when not connected, new_images is returned as-is.",
                ),
                io.Int.Input(
                    "overlap",
                    default=5,
                    min=0,
                    max=4096,
                    step=1,
                    tooltip=(
                        "Number of overlapping frames between source and new images for blending modes.\n"
                        "0 = hard cut: the two batches are concatenated directly, overlap_mode is ignored.\n"
                        "For match_* modes (pure): defines how many frames from each end are scanned — "
                        "overlap_side=both scans last N of source AND first N of new_images; "
                        "source/new_images pins one side and scans only the other.\n"
                        "For match_*+blend hybrid modes: used as both the search window size AND "
                        "the blend window applied starting at the matched cut point."
                    ),
                ),
                io.Combo.Input(
                    "overlap_side",
                    options=_OVERLAP_SIDE_OPTIONS,
                    default="source",
                    tooltip=(
                        "Controls which side(s) are searched or trimmed. Meaning depends on overlap_mode.\n"
                        "\nFor cut mode:\n"
                        "  source: drops the last N frames of source_images.\n"
                        "  new_images: drops the first N frames of new_images.\n"
                        "  both: drops last N from source AND first N from new_images simultaneously\n"
                        "  (removes generated ramp/fade frames from both edges at once).\n"
                        "\nFor match_* modes:\n"
                        "  both: scan last N of source × first N of new_images — find the best-matching pair from both windows (default).\n"
                        "  source: pin new_images[0] as reference, search only source's tail — finds where source best matches the start of new_images.\n"
                        "  new_images: pin source[-1] as reference, search only new_images' head — finds where new_images best matches the end of source.\n"
                        "\nIgnored for all other blend modes."
                    ),
                ),
                io.Combo.Input(
                    "overlap_mode",
                    options=_OVERLAP_MODE_OPTIONS,
                    default="pyramid_blend",
                    tooltip=(
                        "How to blend the overlapping region.\n"
                        "linear_blend: linear cross-fade.\n"
                        "ease_in_out: S-curve cross-fade (slow start/end).\n"
                        "filmic_crossfade: gamma-correct (2.2) cross-fade — better in bright regions.\n"
                        "perceptual_crossfade: LAB-space blend — avoids hue shifts across strong color differences (requires kornia).\n"
                        "average: fixed 50/50 mix of all overlap frames — no directional fade, both generations contribute equally.\n"
                        "dissolve: random pixel-level selection per frame (dithered transition, film-grain look).\n"
                        "pyramid_blend: multi-scale Laplacian pyramid — each frequency band blended independently; sharpest quality.\n"
                        "clock_wipe: clockwise sweep from 12 o'clock — a hard edge rotates across the frame like a clock hand.\n"
                        "clock_wipe_ccw: same sweep counter-clockwise.\n"
                        "wipe_left: hard edge sweeps left→right across the frame.\n"
                        "wipe_right: hard edge sweeps right→left.\n"
                        "wipe_top: hard edge sweeps top→bottom.\n"
                        "wipe_bottom: hard edge sweeps bottom→top.\n"
                        "cut: drops overlap frames — overlap_side=source removes last N from source; overlap_side=new_images removes first N from new_images; overlap_side=both removes last N from source AND first N from new_images simultaneously.\n"
                        "concat: direct concatenation, no frames lost, overlap parameter ignored.\n"
                        "match_ncc / match_mse / match_luminance_mse / match_gradient_mse: scan frames "
                        "of source against frames of new_images (window size = overlap) and hard-cut at the most "
                        "visually similar pair — no blending needed since the frames already match. "
                        "overlap_side=both searches both ends (default); source pins new_images[0] as reference; "
                        "new_images pins source[-1] as reference. "
                        "ncc is invariant to brightness/color drift; mse is fastest; luminance_mse ignores hue; "
                        "gradient_mse matches structure only.\n"
                        "match_*+linear / match_*+pyramid: hybrid — finds the best matching pair exactly as above, "
                        "then instead of a hard cut, applies a linear or pyramid blend window of `overlap` frames "
                        "starting at that matched position. Solves the model ramp-in freeze: the blend smooths "
                        "the near-static tail/head frames rather than cutting through them abruptly."
                    ),
                ),
                io.Image.Input(
                    "new_images",
                    optional=True,
                    tooltip="The new (second) image batch to extend with. Optional — when not connected, source_images is returned as-is.",
                ),
            ],
            outputs=[
                io.Image.Output(
                    "images",
                    tooltip="The extended image batch with the overlap region blended, or whichever single input was connected.",
                ),
            ],
        )

    @classmethod
    def execute(cls, overlap, overlap_side, overlap_mode, source_images=None, new_images=None):
        # Single-input passthrough.
        if source_images is None and new_images is None:
            raise ValueError("At least one of source_images or new_images must be connected.")
        if source_images is None:
            return io.NodeOutput(new_images)
        if new_images is None:
            return io.NodeOutput(source_images)

        # Resize new_images to match source if dimensions differ.
        target_h, target_w = source_images.shape[1], source_images.shape[2]
        if new_images.shape[1] != target_h or new_images.shape[2] != target_w:
            log.warning(
                _LOG_PREFIX,
                f"new_images size {new_images.shape[2]}x{new_images.shape[1]} differs from "
                f"source_images {target_w}x{target_h} — center-cropping and resizing new_images to match.",
            )
            new_images = _resize_to_match(new_images, target_h, target_w)

        # Hard cut: overlap=0 → concatenate directly, no blending, no frame loss.
        # Must be intercepted here because source[:-0] == source[:0] (empty) in Python.
        if overlap == 0:
            log.msg(_LOG_PREFIX, f"overlap=0: hard cut — concatenating {len(source_images)} + {len(new_images)} frames.")
            return io.NodeOutput(torch.cat((source_images, new_images), dim=0))

        # match_* modes — find the best-matching cut pair, then either hard-cut or blend a window.
        if overlap_mode.startswith("match_"):
            rest = overlap_mode[len("match_"):]
            if "+" in rest:
                metric, blend_type = rest.split("+", 1)
            else:
                metric, blend_type = rest, None
            try:
                src_cut, new_cut, match_score = _find_cross_batch_match(
                    source_images, new_images, overlap, metric, side=overlap_side
                )
                if blend_type is None:
                    # Pure hard cut at the match point.
                    log.msg(
                        _LOG_PREFIX,
                        f"Match cut ({metric}, side={overlap_side}): src={src_cut}/{len(source_images) - 1}, "
                        f"new={new_cut}/{len(new_images) - 1}, score={match_score:.5f}",
                    )
                    return io.NodeOutput(torch.cat((source_images[:src_cut + 1], new_images[new_cut:]), dim=0))
                else:
                    # Hybrid: blend a window of `overlap` frames starting at the match point.
                    actual_w = min(overlap, len(source_images) - src_cut, len(new_images) - new_cut)
                    log.msg(
                        _LOG_PREFIX,
                        f"Match+blend ({metric}+{blend_type}, side={overlap_side}): "
                        f"src={src_cut}/{len(source_images) - 1}, "
                        f"new={new_cut}/{len(new_images) - 1}, score={match_score:.5f}, blend_window={actual_w}",
                    )
                    prefix_h = source_images[:src_cut]
                    blend_src_h = source_images[src_cut : src_cut + actual_w]
                    blend_dst_h = new_images[new_cut : new_cut + actual_w]
                    suffix_h = new_images[new_cut + actual_w:]
                    alpha_h = torch.linspace(0, 1, actual_w + 2, device=blend_src_h.device, dtype=blend_src_h.dtype)[1:-1]
                    alpha_h = alpha_h.view(-1, 1, 1, 1)
                    if blend_type == "pyramid":
                        blended_h = _pyramid_blend(blend_src_h, blend_dst_h, alpha_h)
                    else:  # linear
                        blended_h = (1 - alpha_h) * blend_src_h + alpha_h * blend_dst_h
                    return io.NodeOutput(torch.cat((prefix_h, blended_h, suffix_h), dim=0))
            except Exception as e:
                label = f"match_{metric}" if blend_type is None else f"match_{metric}+{blend_type}"
                log.warning(_LOG_PREFIX, f"{label} failed, falling back to concat: {e}")
                return io.NodeOutput(torch.cat((source_images, new_images), dim=0))

        # Blend modes — compute actual_overlap and run the blend.
        # Clamp to the shorter of the two batches so slicing never produces empty tensors.
        actual_overlap = min(overlap, len(source_images), len(new_images))
        if actual_overlap != overlap:
            log.warning(
                _LOG_PREFIX,
                f"overlap ({overlap}) exceeds batch length (source={len(source_images)}, new={len(new_images)}), clamped to {actual_overlap}.",
            )

        prefix = source_images[:-actual_overlap]

        blend_src = source_images[-actual_overlap:]
        blend_dst = new_images[:actual_overlap]

        suffix = new_images[actual_overlap:]

        if overlap_mode == "cut":
            if overlap_side == "source":
                extended_images = torch.cat((source_images[:-actual_overlap], new_images), dim=0)
            elif overlap_side == "both":
                extended_images = torch.cat((source_images[:-actual_overlap], new_images[actual_overlap:]), dim=0)
            else:  # new_images
                extended_images = torch.cat((source_images, new_images[actual_overlap:]), dim=0)

        elif overlap_mode == "clock_wipe":
            # Clockwise sweep from 12 o'clock: a hard edge rotates across the frame,
            # gradually replacing src pixels with dst pixels each frame.
            N, H, W, _C = blend_src.shape
            mask = _clock_wipe_mask(N, H, W, blend_src.device, blend_src.dtype)  # [N, H, W, 1]
            blended = torch.where(mask.expand_as(blend_src), blend_dst, blend_src)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "clock_wipe_ccw":
            N, H, W, _C = blend_src.shape
            mask = _clock_wipe_mask(N, H, W, blend_src.device, blend_src.dtype, clockwise=False)
            blended = torch.where(mask.expand_as(blend_src), blend_dst, blend_src)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode in ("wipe_left", "wipe_right", "wipe_top", "wipe_bottom"):
            N, H, W, _C = blend_src.shape
            direction = overlap_mode[len("wipe_"):]
            mask = _linear_wipe_mask(N, H, W, blend_src.device, blend_src.dtype, direction)
            blended = torch.where(mask.expand_as(blend_src), blend_dst, blend_src)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "linear_blend":
            alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            alpha = alpha.view(-1, 1, 1, 1)
            blended = (1 - alpha) * blend_src + alpha * blend_dst
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "ease_in_out":
            t = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            eased = 3 * t * t - 2 * t * t * t
            eased = eased.view(-1, 1, 1, 1)
            blended = (1 - eased) * blend_src + eased * blend_dst
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "filmic_crossfade":
            gamma = 2.2
            alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            alpha = alpha.view(-1, 1, 1, 1)
            linear_src = torch.pow(blend_src.clamp(min=0), gamma)
            linear_dst = torch.pow(blend_dst.clamp(min=0), gamma)
            blended = torch.pow(((1 - alpha) * linear_src + alpha * linear_dst).clamp(min=0), 1.0 / gamma)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "perceptual_crossfade":
            try:
                import kornia  # type: ignore
            except ImportError:
                log.warning(
                    _LOG_PREFIX,
                    "kornia is required for perceptual_crossfade — falling back to linear_blend. "
                    "Install with: pip install kornia",
                )
                alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
                alpha = alpha.view(-1, 1, 1, 1)
                blended = (1 - alpha) * blend_src + alpha * blend_dst
                extended_images = torch.cat((prefix, blended, suffix), dim=0)
            else:
                alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
                alpha = alpha.view(-1, 1, 1, 1)
                src_nchw = blend_src.movedim(-1, 1)
                dst_nchw = blend_dst.movedim(-1, 1)
                lab_src = kornia.color.rgb_to_lab(src_nchw)
                lab_dst = kornia.color.rgb_to_lab(dst_nchw)
                blended_lab = (1 - alpha) * lab_src + alpha * lab_dst
                blended_rgb = kornia.color.lab_to_rgb(blended_lab)
                blended = blended_rgb.movedim(1, -1)
                extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "average":
            # Fixed 50/50 blend across all overlap frames — no directional fade.
            blended = 0.5 * blend_src + 0.5 * blend_dst
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "dissolve":
            # Per-pixel random selection: at frame i, alpha[i] fraction of pixels come from dst.
            # Creates a dithered/grain-textured transition instead of a smooth fade.
            alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            noise = torch.rand(
                actual_overlap, blend_src.shape[1], blend_src.shape[2], 1,
                device=blend_src.device, dtype=blend_src.dtype,
            )
            mask = noise < alpha.view(-1, 1, 1, 1)
            blended = torch.where(mask, blend_dst, blend_src)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "pyramid_blend":
            # Multi-scale Laplacian pyramid blend — each frequency band blended independently.
            alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            alpha = alpha.view(-1, 1, 1, 1)
            blended = _pyramid_blend(blend_src, blend_dst, alpha)
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        elif overlap_mode == "concat":
            # Direct concatenation — no frame loss, overlap parameter is ignored.
            extended_images = torch.cat((source_images, new_images), dim=0)

        else:
            log.warning(_LOG_PREFIX, f"Unknown overlap_mode '{overlap_mode}', falling back to linear_blend.")
            alpha = torch.linspace(0, 1, actual_overlap + 2, device=blend_src.device, dtype=blend_src.dtype)[1:-1]
            alpha = alpha.view(-1, 1, 1, 1)
            blended = (1 - alpha) * blend_src + alpha * blend_dst
            extended_images = torch.cat((prefix, blended, suffix), dim=0)

        return io.NodeOutput(extended_images)
