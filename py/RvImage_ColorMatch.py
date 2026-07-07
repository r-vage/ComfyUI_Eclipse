#
# Color Match node — transfer color grading from a reference image to a target image.
# Ported from KJNodes ColorMatchV2, with image (target) as first input so bypass
# passes the correct image through.
#
# Methods:
#   CPU (color-matcher): mkl, hm, reinhard, mvgd, hm-mvgd-hm, hm-mkl-hm
#   GPU (Kornia): reinhard_lab_gpu
#   GPU (torch): wavelet, scattersort
#

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np  # type: ignore
import torch  # type: ignore
import comfy.model_management as model_management  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.image_helpers import unwrap_value, flatten_images, was_input_batch, prepare_image_output
from ..core.logger import log

_LOG_PREFIX = "ColorMatch"

# ============================================================================
# Wavelet Color Transfer (Haar)
# ============================================================================
# Decomposes both images into Haar wavelets, transfers LL (low-frequency/color)
# statistics from ref to target via AdaIN (mean/std normalization in LAB space),
# preserves target's detail bands (LH, HL, HH), then reconstructs.


def _haar_decompose(x):
    # Orthonormal Haar wavelet decomposition.
    # Input: [B, C, H, W] → Output: LL, LH, HL, HH each [B, C, H//2, W//2]
    x = x.float()
    x00 = x[:, :, 0::2, 0::2]
    x01 = x[:, :, 0::2, 1::2]
    x10 = x[:, :, 1::2, 0::2]
    x11 = x[:, :, 1::2, 1::2]
    norm = 0.5 / (2**0.5)
    LL = (x00 + x01 + x10 + x11) * norm
    LH = (x00 - x01 + x10 - x11) * norm
    HL = (x00 + x01 - x10 - x11) * norm
    HH = (x00 - x01 - x10 + x11) * norm
    return LL, LH, HL, HH


def _haar_reconstruct(LL, LH, HL, HH):
    # Orthonormal inverse Haar reconstruction.
    # Input: LL, LH, HL, HH [B, C, H, W] → Output: [B, C, H*2, W*2]
    norm = 1.0 / (2**0.5)
    B, C, H, W = LL.shape
    x00 = (LL + LH + HL + HH) * norm
    x01 = (LL - LH + HL - HH) * norm
    x10 = (LL + LH - HL - HH) * norm
    x11 = (LL - LH - HL + HH) * norm
    out = torch.zeros(B, C, H * 2, W * 2, device=LL.device, dtype=LL.dtype)
    out[:, :, 0::2, 0::2] = x00
    out[:, :, 0::2, 1::2] = x01
    out[:, :, 1::2, 0::2] = x10
    out[:, :, 1::2, 1::2] = x11
    return out


def _wavelet_color_transfer(src_bchw, ref_bchw, strength):
    # Transfer color via Haar wavelets: match LL band statistics, keep detail bands.
    # Operates in LAB space for perceptually uniform transfer.
    import kornia  # type: ignore

    src_lab = kornia.color.rgb_to_lab(src_bchw)

    # Resize ref to match src dimensions (we only need color statistics, not spatial layout)
    _, _, src_H, src_W = src_bchw.shape
    _, _, ref_H, ref_W = ref_bchw.shape
    if ref_H != src_H or ref_W != src_W:
        ref_resized = torch.nn.functional.interpolate(
            ref_bchw, size=(src_H, src_W), mode="bilinear", align_corners=False
        )
    else:
        ref_resized = ref_bchw
    ref_lab = kornia.color.rgb_to_lab(ref_resized)

    B = src_lab.shape[0]
    _, _, H, W = src_lab.shape

    # Pad to even dimensions if needed
    pad_h = H % 2
    pad_w = W % 2
    if pad_h or pad_w:
        src_lab = torch.nn.functional.pad(src_lab, (0, pad_w, 0, pad_h), mode="reflect")
        ref_lab = torch.nn.functional.pad(ref_lab, (0, pad_w, 0, pad_h), mode="reflect")

    src_LL, src_LH, src_HL, src_HH = _haar_decompose(src_lab)
    ref_LL, _, _, _ = _haar_decompose(ref_lab)

    # AdaIN on LL band: normalize src LL, scale/shift by ref LL statistics
    for b in range(B):
        ref_idx = min(b, ref_LL.shape[0] - 1)
        for c in range(3):  # L, A, B channels
            s_mean = src_LL[b, c].mean()
            s_std = src_LL[b, c].std().clamp(min=1e-6)
            r_mean = ref_LL[ref_idx, c].mean()
            r_std = ref_LL[ref_idx, c].std().clamp(min=1e-6)
            src_LL[b, c] = (src_LL[b, c] - s_mean) / s_std * r_std + r_mean

    # Reconstruct with matched LL + original detail bands
    out_lab = _haar_reconstruct(src_LL, src_LH, src_HL, src_HH)

    # Remove padding
    if pad_h or pad_w:
        out_lab = out_lab[:, :, :H, :W]

    out_rgb = kornia.color.lab_to_rgb(out_lab)

    # Apply strength blending
    if strength != 1.0:
        out_rgb = (1.0 - strength) * src_bchw + strength * out_rgb

    return out_rgb.clamp_(0, 1)


# ============================================================================
# Scattersort (Exact Histogram Matching)
# ============================================================================
# Sorts pixel values per-channel and maps target's rank positions to reference's
# sorted values. Produces exact color distribution match.


def _scattersort_transfer(src_bchw, ref_bchw, strength):
    # Per-channel histogram matching via sort-scatter.
    B, C, H, W = src_bchw.shape
    src_flat = src_bchw.reshape(B, C, -1)  # [B, C, N]
    ref_flat = ref_bchw.reshape(ref_bchw.shape[0], C, -1)

    out_flat = src_flat.clone()
    for b in range(B):
        ref_idx = min(b, ref_flat.shape[0] - 1)
        for c in range(C):
            src_ch = src_flat[b, c]  # [N]
            ref_ch = ref_flat[ref_idx, c]

            # Sort reference values
            ref_sorted = ref_ch.sort()[0]

            # If different pixel counts, interpolate reference distribution
            if src_ch.numel() != ref_ch.numel():
                # Resample ref distribution to src length
                ref_sorted = torch.nn.functional.interpolate(
                    ref_sorted.unsqueeze(0).unsqueeze(0),
                    size=src_ch.numel(),
                    mode="linear",
                    align_corners=True,
                ).squeeze()

            # Get sort indices for src, scatter ref sorted values back
            src_idx = src_ch.argsort()
            out_flat[b, c].scatter_(0, src_idx, ref_sorted)

    if strength != 1.0:
        out_flat = (1.0 - strength) * src_flat + strength * out_flat

    return out_flat.reshape(B, C, H, W).clamp_(0, 1)





class RvImage_ColorMatch(io.ComfyNode):
    @staticmethod
    def _sanitize_output(output):
        # Keep invalid pixels from propagating to preview or downstream image nodes.
        return torch.nan_to_num(output, nan=0.0, posinf=1.0, neginf=0.0).clamp_(0, 1)

    @staticmethod
    def _cpu_matcher_input(tensor, method, frame_index, input_name):
        # color-matcher mutates its arrays, so always supply an independent CPU copy.
        image_np = tensor.detach().cpu().numpy().copy()
        finite = np.isfinite(image_np)
        out_of_range = finite & ((image_np < 0.0) | (image_np > 1.0))
        if not finite.all() or out_of_range.any():
            finite_values = image_np[finite]
            value_range = (
                f"{finite_values.min():.6g} to {finite_values.max():.6g}"
                if finite_values.size
                else "no finite values"
            )
            log.warning(
                _LOG_PREFIX,
                f"Frame {frame_index} {input_name} for {method} contains non-finite or "
                f"out-of-range RGB values ({value_range}); normalizing to [0, 1].",
            )
        np.nan_to_num(image_np, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        np.clip(image_np, 0.0, 1.0, out=image_np)
        if method == "reinhard":
            # Reinhard takes log10 of LMS values; zero is not safe for that conversion.
            np.maximum(image_np, 1.0 / 255.0, out=image_np)
        return image_np

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Color Match [Eclipse]",
            display_name="Color Match",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_FX.value,
            description=(
                "Transfer color grading from a reference image onto a target image. "
                "CPU methods (color-matcher): mkl, hm, reinhard, mvgd, hm-mvgd-hm, hm-mkl-hm. "
                "GPU methods: reinhard_lab_gpu (Kornia), wavelet (Haar wavelet LAB transfer), "
                "scattersort (exact histogram matching). CPU inputs are normalized to finite RGB "
                "values for stable processing; invalid results fall back to the target image. "
                "Based on KJNodes ColorMatch."
            ),
            is_input_list=True,
            inputs=[
                io.Image.Input(
                    "image",
                    tooltip="Target image to apply color grading to. Passes through on bypass.",
                ),
                io.Image.Input(
                    "image_ref",
                    tooltip="Reference image whose colors will be transferred.",
                ),
                io.Combo.Input(
                    "method",
                    options=[
                        "mkl",
                        "hm",
                        "reinhard",
                        "mvgd",
                        "hm-mvgd-hm",
                        "hm-mkl-hm",
                        "reinhard_lab_gpu",
                        "wavelet",
                        "scattersort",
                    ],
                    default="mkl",
                    tooltip=(
                        "Color transfer algorithm. wavelet = Haar wavelet LAB transfer (preserves detail). "
                        "scattersort = exact histogram matching per channel. CPU methods normalize "
                        "non-finite or out-of-range RGB inputs for numerical stability."
                    ),
                ),
                io.Float.Input(
                    "strength",
                    default=1.0,
                    min=0.0,
                    max=10.0,
                    step=0.01,
                    tooltip="Blend strength. 0 = no change, 1 = full transfer.",
                ),
                io.Boolean.Input(
                    "multithread",
                    default=True,
                    tooltip="Use multithreading for batch processing.",
                ),
                io.Boolean.Input(
                    "per_frame",
                    default=False,
                    tooltip="Process each frame independently instead of the whole batch at once. "
                    "Caps VRAM usage to one frame at a time for GPU methods; slightly slower "
                    "but avoids out-of-memory errors on large batches.",
                ),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls, image, image_ref, method, strength=1.0, multithread=True, per_frame=False
    ):
        method = unwrap_value(method, "mkl")
        strength = unwrap_value(strength, 1.0)
        multithread = unwrap_value(multithread, True)
        per_frame = unwrap_value(per_frame, False)

        if strength == 0:
            return io.NodeOutput(image)

        flat_image = flatten_images(image)
        flat_ref = flatten_images(image_ref)

        if not flat_image or not flat_ref:
            raise ValueError("Color Match: No images provided in input/reference.")

        was_batch = was_input_batch(image)

        processed_list = []
        for i, img in enumerate(flat_image):
            ref_img = flat_ref[min(i, len(flat_ref) - 1)] if per_frame else flat_ref[0]
            out = cls._process_batch(img, ref_img, method, strength, multithread)
            processed_list.append(out)

        merged_tensor = torch.cat(processed_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)

    @classmethod
    def _process_batch(cls, image, image_ref, method, strength, multithread):
        # GPU path — Kornia reinhard in Lab space
        if method == "reinhard_lab_gpu":
            import kornia  # type: ignore

            device = model_management.get_torch_device()

            B, H, W, C = image.shape

            src_bchw = image.to(device).permute(0, 3, 1, 2).contiguous()
            ref_bchw = image_ref.to(device).permute(0, 3, 1, 2).contiguous()

            src_lab = kornia.color.rgb_to_lab(src_bchw)
            ref_lab = kornia.color.rgb_to_lab(ref_bchw)

            src_lab_flat = src_lab.view(B, C, -1)
            ref_lab_flat = ref_lab.view(ref_lab.shape[0], C, -1)

            src_std, src_mean = torch.std_mean(
                src_lab_flat, dim=-1, keepdim=True, unbiased=False
            )
            ref_std, ref_mean = torch.std_mean(
                ref_lab_flat, dim=-1, keepdim=True, unbiased=False
            )
            src_std = src_std.clamp_min_(1e-6)

            if ref_lab.shape[0] == 1 and B > 1:
                ref_mean = ref_mean.expand(B, -1, -1)
                ref_std = ref_std.expand(B, -1, -1)

            corrected_lab_flat = (src_lab_flat - src_mean) * (
                ref_std / src_std
            ) + ref_mean
            corrected_lab = corrected_lab_flat.view(B, C, H, W)

            corrected_rgb = kornia.color.lab_to_rgb(corrected_lab)
            out = (1.0 - strength) * src_bchw + strength * corrected_rgb
            out = out.permute(0, 2, 3, 1).contiguous()

            return cls._sanitize_output(out.cpu().float())

        # GPU path — Haar wavelet color transfer in LAB space
        if method == "wavelet":
            device = model_management.get_torch_device()
            src_bchw = image.to(device).permute(0, 3, 1, 2).contiguous()
            ref_bchw = image_ref.to(device).permute(0, 3, 1, 2).contiguous()
            out = _wavelet_color_transfer(src_bchw, ref_bchw, strength)
            out = out.permute(0, 2, 3, 1).contiguous()
            return cls._sanitize_output(out.cpu().float())

        # GPU path — scattersort exact histogram matching
        if method == "scattersort":
            device = model_management.get_torch_device()
            src_bchw = image.to(device).permute(0, 3, 1, 2).contiguous()
            ref_bchw = image_ref.to(device).permute(0, 3, 1, 2).contiguous()
            out = _scattersort_transfer(src_bchw, ref_bchw, strength)
            out = out.permute(0, 2, 3, 1).contiguous()
            return cls._sanitize_output(out.cpu().float())

        # CPU path — color-matcher library
        try:
            from color_matcher import ColorMatcher  # type: ignore
        except ImportError:
            raise ImportError(
                "color-matcher is not installed. Install with: pip install color-matcher"
            )

        batch_size = image.size(0)
        ref_batch_size = image_ref.size(0)

        def process(i):  # noqa: E306
            cm = ColorMatcher()
            target_np = cls._cpu_matcher_input(image[i], method, i, "target")
            ref_np = cls._cpu_matcher_input(
                image_ref[min(i, ref_batch_size - 1)], method, i, "reference"
            )
            target_fallback = target_np.copy()
            try:
                result = cm.transfer(src=target_np, ref=ref_np, method=method)
                if result.shape != target_np.shape or not np.isfinite(result).all():
                    log.warning(
                        _LOG_PREFIX,
                        f"Frame {i} {method} returned an invalid result; using the target image.",
                    )
                    return torch.from_numpy(target_fallback)
                if strength != 1:
                    result = target_np + strength * (result - target_np)
                return torch.from_numpy(result)
            except Exception as e:
                log.error(_LOG_PREFIX, f"Frame {i} {method} error: {e}")
                return torch.from_numpy(target_fallback)

        if multithread and batch_size > 1:
            max_threads = min(os.cpu_count() or 1, batch_size)
            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                out = list(executor.map(process, range(batch_size)))
        else:
            out = [process(i) for i in range(batch_size)]

        out = torch.stack(out, dim=0).to(torch.float32)
        return cls._sanitize_output(out)
