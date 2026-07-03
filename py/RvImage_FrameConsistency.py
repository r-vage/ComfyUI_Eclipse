#
# Image Frame Consistency — post-process a video frame batch to reduce
# quality drift and colour shifts that occur between sliding context windows
# (WAN, CogVideo, etc.).
#
# Techniques (each individually switchable):
#   • Section Colour Normalise  — aligns mean/std of each window to window 0
#   • Histogram Match           — matches every frame's histogram to a reference frame
#   • Luminance Normalise       — corrects brightness drift in LAB L-channel only
#   • Temporal Smooth           — reduces per-frame flicker with weighted neighbour blend
#   • Sharpen Recover           — adaptive unsharp mask to recover sharpness drift
#

import comfy.utils  # type: ignore
import numpy as np  # type: ignore
import cv2  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "VideoFrameConsistency"


# ---------------------------------------------------------------------------
# Internal helpers — all operate on (N, H, W, 3) uint8 arrays
# ---------------------------------------------------------------------------

def _to_u8(frames_f32: np.ndarray) -> np.ndarray:
    # (N, H, W, 3) float32 [0,1] → uint8 [0,255]
    return (np.clip(frames_f32, 0.0, 1.0) * 255.0).astype(np.uint8)


def _to_f32(frames_u8: np.ndarray) -> np.ndarray:
    # (N, H, W, 3) uint8 [0,255] → float32 [0,1]
    return frames_u8.astype(np.float32) / 255.0


def _section_normalise(frames_u8: np.ndarray, window_size: int) -> np.ndarray:
    # Align colour mean and std-dev of every window to the first window.
    #
    # Corrects the biggest cross-section global colour/brightness shifts.
    frames = frames_u8.astype(np.float32)
    n = len(frames)
    ref = frames[:window_size]
    ref_mean = ref.mean(axis=(0, 1, 2))
    ref_std  = ref.std(axis=(0, 1, 2))
    out = frames.copy()
    for start in range(window_size, n, window_size):
        end = min(start + window_size, n)
        sec = frames[start:end]
        sec_mean = sec.mean(axis=(0, 1, 2))
        sec_std  = sec.std(axis=(0, 1, 2)) + 1e-8
        normalised = (sec - sec_mean) / sec_std * ref_std + ref_mean
        out[start:end] = np.clip(normalised, 0.0, 255.0)
    return out.astype(np.uint8)


def _histogram_match(frames_u8: np.ndarray, ref_frame_u8: np.ndarray, strength: float) -> np.ndarray:
    # Match per-channel histogram of every frame to ref_frame_u8, blended
    # by `strength` (0 = no change, 1 = full match).
    # Matching a frame against itself is a no-op so no skip is needed.
    try:
        from skimage import exposure  # type: ignore
    except ImportError:
        log.warning(_LOG_PREFIX, "scikit-image not found — skipping histogram match.")
        return frames_u8

    out = frames_u8.copy()
    for i in range(len(frames_u8)):
        matched = exposure.match_histograms(frames_u8[i], ref_frame_u8, channel_axis=-1)
        blended = (
            frames_u8[i].astype(np.float32) * (1.0 - strength)
            + matched.astype(np.float32) * strength
        )
        out[i] = np.clip(blended, 0.0, 255.0).astype(np.uint8)
    return out


def _luminance_normalise(frames_u8: np.ndarray, ref_frame_u8: np.ndarray) -> np.ndarray:
    # Correct luminance drift using LAB colour space — only adjusts the L channel
    # so hue and chroma are preserved.
    ref_lab = cv2.cvtColor(ref_frame_u8, cv2.COLOR_RGB2LAB).astype(np.float32)
    ref_L_mean = ref_lab[..., 0].mean()
    ref_L_std  = ref_lab[..., 0].std() + 1e-8
    out = []
    for frame in frames_u8:
        lab = cv2.cvtColor(frame, cv2.COLOR_RGB2LAB).astype(np.float32)
        L = lab[..., 0]
        L_norm = (L - L.mean()) / (L.std() + 1e-8) * ref_L_std + ref_L_mean
        lab[..., 0] = np.clip(L_norm, 0.0, 255.0)
        out.append(cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB))
    return np.array(out, dtype=np.uint8)


def _temporal_smooth(frames_u8: np.ndarray, radius: int) -> np.ndarray:
    # Gaussian-weighted temporal blend over ±radius frames to reduce flicker.
    #
    # Frame 0 is always kept untouched — it is the sharpest anchor frame and
    # must not be softened by blending with its (potentially softer) neighbours.
    n = len(frames_u8)
    size = 2 * radius + 1
    # Build Gaussian kernel
    k = np.array([
        np.exp(-0.5 * ((i - radius) / max(radius, 1)) ** 2)
        for i in range(size)
    ], dtype=np.float32)
    k /= k.sum()
    out = np.zeros_like(frames_u8, dtype=np.float32)
    out[0] = frames_u8[0].astype(np.float32)
    for i in range(1, n):
        for j, w in enumerate(k):
            idx = int(np.clip(i - radius + j, 0, n - 1))
            out[i] += frames_u8[idx].astype(np.float32) * w
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def _sharpen_recover(frames_u8: np.ndarray, base_strength: float, ramp: float,
                     skip_frame0: bool = True) -> np.ndarray:
    # Apply progressively stronger unsharp mask to recover sharpness drift.
    #
    # When skip_frame0 is True (no external reference provided), frame 0 is the
    # sharpest anchor and is passed through untouched; the ramp starts at frame 1.
    # When skip_frame0 is False (external ref_image provided), frame 0 has drifted
    # just like the rest and is also corrected; the ramp starts at frame 0.
    start_idx = 1 if skip_frame0 else 0
    out = [frames_u8[0]] if skip_frame0 else []
    for i, frame in enumerate(frames_u8[start_idx:], start=start_idx):
        strength = min(base_strength + (i - start_idx) * ramp, 2.0)
        blurred = cv2.GaussianBlur(frame, (0, 0), sigmaX=2.0)
        sharpened = cv2.addWeighted(frame, 1.0 + strength, blurred, -strength, 0)
        out.append(np.clip(sharpened, 0, 255).astype(np.uint8))
    return np.array(out, dtype=np.uint8)


# ---------------------------------------------------------------------------
# ComfyUI Node
# ---------------------------------------------------------------------------

class RvImage_FrameConsistency(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Video Frame Consistency [Eclipse]",
            display_name="Video Frame Consistency",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description=(
                "Reduce quality drift and colour shifts between sliding context windows "
                "in WAN / CogVideo video generation. "
                "Each technique can be toggled independently."
            ),
            inputs=[
                io.Image.Input("image",
                               tooltip="Batch of video frames (N, H, W, 3)."),
                io.Image.Input("ref_image", optional=True,
                               tooltip="Optional reference image (e.g. the original input image). "
                                       "When connected, all methods use this as the colour/sharpness target "
                                       "instead of a frame from the batch. Frame 0 is also corrected. "
                                       "When disconnected, reference_frame selects the batch frame to use."),

                # --- Context window ---
                io.Int.Input("window_size", default=81, min=1, max=512, step=1,
                             tooltip="Frames per context window used during generation "
                                     "(e.g. 81 for WAN 5 s @ 16 fps). Used by Section Normalise."),
                io.Int.Input("reference_frame", default=0, min=0, max=4096, step=1,
                             tooltip="Index of the frame used as the colour/quality reference "
                                     "(0 = first frame). Used by Histogram Match and Luminance Normalise "
                                     "when no ref_image is connected."),

                # --- Section colour normalise ---
                io.Boolean.Input("section_normalise", default=True,
                                 label_on="On", label_off="Off",
                                 tooltip="Align the colour mean and std-dev of each window to the first window. "
                                         "Corrects large cross-section shifts."),

                # --- Histogram match ---
                io.Boolean.Input("hist_match", default=True,
                                 label_on="On", label_off="Off",
                                 tooltip="Match per-channel histograms of every frame to the reference frame."),
                io.Float.Input("hist_strength", default=0.6, min=0.0, max=1.0, step=0.05,
                               tooltip="Blend factor: 0 = no change, 1 = full histogram match."),

                # --- Luminance normalise ---
                io.Boolean.Input("luminance_normalise", default=True,
                                 label_on="On", label_off="Off",
                                 tooltip="Correct brightness drift in LAB L-channel only, preserving hue and chroma."),

                # --- Temporal smooth ---
                io.Boolean.Input("temporal_smooth", default=False,
                                 label_on="On", label_off="Off",
                                 tooltip="Reduce per-frame flicker by blending each frame with its neighbours "
                                         "using Gaussian weights."),
                io.Int.Input("temporal_radius", default=1, min=1, max=8, step=1,
                             tooltip="Number of neighbour frames on each side to include in the temporal blend."),

                # --- Sharpen recover ---
                io.Boolean.Input("sharpen_recover", default=False,
                                 label_on="On", label_off="Off",
                                 tooltip="Apply progressively stronger unsharp mask to later frames "
                                         "to recover sharpness lost across generation windows."),
                io.Float.Input("sharpen_base", default=0.3, min=0.0, max=2.0, step=0.05,
                               tooltip="Base unsharp-mask strength applied from frame 0."),
                io.Float.Input("sharpen_ramp", default=0.003, min=0.0, max=0.05, step=0.001,
                               tooltip="Additional sharpening strength added per frame index "
                                       "(0.003 ≈ +0.24 over 81 frames)."),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(
        cls,
        image,
        window_size: int = 81,
        reference_frame: int = 0,
        section_normalise: bool = True,
        hist_match: bool = True,
        hist_strength: float = 0.6,
        luminance_normalise: bool = True,
        temporal_smooth: bool = False,
        temporal_radius: int = 1,
        sharpen_recover: bool = False,
        sharpen_base: float = 0.3,
        sharpen_ramp: float = 0.003,
        ref_image=None,
    ):
        import torch  # type: ignore

        # Ensure (N, H, W, 3)
        if image.dim() == 3:
            image = image.unsqueeze(0)

        n = image.shape[0]
        ref_idx = int(np.clip(reference_frame, 0, n - 1))

        # Convert to numpy uint8 for CV / skimage operations
        frames_np = image.cpu().float().numpy()
        frames_u8 = _to_u8(frames_np)

        # Resolve reference frame — external image takes priority over batch frame index.
        # When ref_image is provided every frame (including frame 0) is corrected against it.
        has_ext_ref = ref_image is not None
        if has_ext_ref:
            ref_np = ref_image.cpu().float().numpy()
            if ref_np.ndim == 4:
                ref_np = ref_np[0]  # take first frame if batch
            ref_frame_u8 = _to_u8(ref_np[np.newaxis])[0]
            log.msg(_LOG_PREFIX, "Using external ref_image as colour/sharpness reference")
        else:
            ref_frame_u8 = frames_u8[ref_idx]

        # Count enabled steps for the progress bar
        enabled = [
            section_normalise and n > window_size,
            hist_match,
            luminance_normalise,
            temporal_smooth,
            sharpen_recover,
        ]
        total_steps = sum(enabled)
        pbar = comfy.utils.ProgressBar(max(total_steps, 1))
        applied: list[str] = []

        # 1. Section colour normalise — biggest global drift fix, do first
        if section_normalise and n > window_size:
            frames_u8 = _section_normalise(frames_u8, window_size)
            applied.append("section_normalise")
            pbar.update(1)

        # 2. Histogram match
        if hist_match:
            frames_u8 = _histogram_match(frames_u8, ref_frame_u8, hist_strength)
            applied.append("hist_match")
            pbar.update(1)

        # 3. Luminance normalise — fine-tunes residual L drift after histogram match
        if luminance_normalise:
            frames_u8 = _luminance_normalise(frames_u8, ref_frame_u8)
            applied.append("luminance_normalise")
            pbar.update(1)

        # 4. Temporal smooth — reduce per-frame flicker
        if temporal_smooth:
            frames_u8 = _temporal_smooth(frames_u8, temporal_radius)
            applied.append("temporal_smooth")
            pbar.update(1)

        # 5. Sharpen recover — compensate for accumulated softness in later frames
        if sharpen_recover:
            frames_u8 = _sharpen_recover(frames_u8, sharpen_base, sharpen_ramp,
                                         skip_frame0=not has_ext_ref)
            applied.append("sharpen_recover")
            pbar.update(1)

        if applied:
            log.msg(_LOG_PREFIX, f"{n} frames | applied: {', '.join(applied)}")
        else:
            log.msg(_LOG_PREFIX, f"{n} frames | no steps enabled — passthrough.")

        out_tensor = torch.from_numpy(_to_f32(frames_u8))
        return io.NodeOutput(out_tensor)
