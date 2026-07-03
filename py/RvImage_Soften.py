#
# Image Soften node — reduce over-sharpening and harsh detail from iterative
# generation (e.g. Flux render→refine chains). Multiple methods from gentle
# blur to frequency-aware detail attenuation.
#
# Methods:
#   gaussian     — Classic Gaussian blur (uniform softening)
#   bilateral    — Edge-preserving smoothing (softens flat areas, keeps edges)
#   wavelet      — Haar wavelet detail attenuation (reduces high-freq while preserving color/structure)
#   median       — Median filter (good for speckle/noise, preserves edges)
#   anisotropic  — Perona-Malik diffusion (iterative edge-preserving smoothing)
#   edge_blur    — Sobel edge detection + selective Gaussian blur at hard edges only
#

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore
import comfy.model_management as model_management  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY


# ============================================================================
# Gaussian Blur
# ============================================================================

def _gaussian_kernel_1d(sigma, device, dtype):
    # Create a 1D Gaussian kernel with radius = ceil(2*sigma).
    radius = max(int(sigma * 2 + 0.5), 1)
    size = 2 * radius + 1
    x = torch.arange(size, device=device, dtype=dtype) - radius
    kernel = torch.exp(-0.5 * (x / max(sigma, 1e-6)) ** 2)
    kernel = kernel / kernel.sum()
    return kernel, radius


def _gaussian_blur(img_bchw, sigma):
    # Separable Gaussian blur on BCHW tensor.
    if sigma <= 0:
        return img_bchw
    kernel, radius = _gaussian_kernel_1d(sigma, img_bchw.device, img_bchw.dtype)
    B, C, H, W = img_bchw.shape
    # Horizontal pass
    kh = kernel.view(1, 1, 1, -1).expand(C, -1, -1, -1)
    out = F.pad(img_bchw, (radius, radius, 0, 0), mode='reflect')
    out = F.conv2d(out, kh, groups=C)
    # Vertical pass
    kv = kernel.view(1, 1, -1, 1).expand(C, -1, -1, -1)
    out = F.pad(out, (0, 0, radius, radius), mode='reflect')
    out = F.conv2d(out, kv, groups=C)
    return out


# ============================================================================
# Bilateral Filter (edge-preserving)
# ============================================================================

def _bilateral_filter(img_bchw, sigma_spatial, sigma_color):
    # Approximated bilateral filter using spatial Gaussian + color range weighting.
    # Works per-pixel with a local window.
    kernel, radius = _gaussian_kernel_1d(sigma_spatial, img_bchw.device, img_bchw.dtype)
    size = 2 * radius + 1
    B, C, H, W = img_bchw.shape

    # Unfold into patches
    padded = F.pad(img_bchw, (radius, radius, radius, radius), mode='reflect')
    patches = padded.unfold(2, size, 1).unfold(3, size, 1)  # [B, C, H, W, kH, kW]

    # Spatial weights [kH, kW]
    ky = kernel.view(-1, 1)
    kx = kernel.view(1, -1)
    spatial_w = (ky * kx).unsqueeze(0).unsqueeze(0).unsqueeze(0).unsqueeze(0)  # [1,1,1,1,kH,kW]

    # Color/range weights — how similar each neighbor pixel is
    center = img_bchw.unsqueeze(-1).unsqueeze(-1)  # [B,C,H,W,1,1]
    color_diff = (patches - center) ** 2
    color_diff = color_diff.sum(dim=1, keepdim=True)  # Sum across channels [B,1,H,W,kH,kW]
    range_w = torch.exp(-color_diff / (2 * max(sigma_color, 1e-6) ** 2))

    # Combined weights
    weights = spatial_w * range_w  # [B,1or C,H,W,kH,kW]
    weights_sum = weights.sum(dim=(-2, -1), keepdim=True).clamp(min=1e-8)
    weights_norm = weights / weights_sum

    # Weighted sum
    out = (patches * weights_norm).sum(dim=(-2, -1))
    return out


# ============================================================================
# Wavelet Softening (Haar)
# ============================================================================

def _haar_decompose(x):
    # Orthonormal Haar wavelet decomposition.
    # [B, C, H, W] → LL, LH, HL, HH each [B, C, H//2, W//2]
    x = x.float()
    x00 = x[:, :, 0::2, 0::2]
    x01 = x[:, :, 0::2, 1::2]
    x10 = x[:, :, 1::2, 0::2]
    x11 = x[:, :, 1::2, 1::2]
    norm = 0.5 / (2 ** 0.5)
    LL = (x00 + x01 + x10 + x11) * norm
    LH = (x00 - x01 + x10 - x11) * norm
    HL = (x00 + x01 - x10 - x11) * norm
    HH = (x00 - x01 - x10 + x11) * norm
    return LL, LH, HL, HH


def _haar_reconstruct(LL, LH, HL, HH):
    # Orthonormal inverse Haar reconstruction.
    # LL, LH, HL, HH [B, C, H, W] → [B, C, H*2, W*2]
    norm = 1.0 / (2 ** 0.5)
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


def _wavelet_soften(img_bchw, strength):
    # Attenuate high-frequency wavelet bands (LH, HL, HH) while preserving
    # the low-frequency LL band (color/structure). strength=1 removes all detail,
    # strength=0.5 halves detail intensity.
    _, _, H, W = img_bchw.shape
    pad_h = H % 2
    pad_w = W % 2
    x = img_bchw
    if pad_h or pad_w:
        x = F.pad(x, (0, pad_w, 0, pad_h), mode='reflect')

    LL, LH, HL, HH = _haar_decompose(x)

    # Attenuate detail bands — strength=1 means full removal
    attenuation = 1.0 - strength
    LH = LH * attenuation
    HL = HL * attenuation
    HH = HH * attenuation

    out = _haar_reconstruct(LL, LH, HL, HH)

    if pad_h or pad_w:
        out = out[:, :, :H, :W]
    return out


# ============================================================================
# Median Filter
# ============================================================================

def _median_filter(img_bchw, radius):
    # Per-channel median filter using unfold.
    r = max(int(radius), 1)
    size = 2 * r + 1
    B, C, H, W = img_bchw.shape
    padded = F.pad(img_bchw, (r, r, r, r), mode='reflect')
    # Unfold into patches per channel
    patches = padded.unfold(2, size, 1).unfold(3, size, 1)  # [B, C, H, W, kH, kW]
    patches = patches.contiguous().view(B, C, H, W, -1)  # [B, C, H, W, kH*kW]
    out = patches.median(dim=-1)[0]
    return out


# ============================================================================
# Anisotropic Diffusion (Perona-Malik)
# ============================================================================

def _anisotropic_diffusion(img_bchw, iterations, kappa, gamma=0.1):
    # Perona-Malik anisotropic diffusion — iterative edge-preserving smoothing.
    # kappa controls edge sensitivity (higher = smoother across edges).
    # gamma is the diffusion rate per step (0 < gamma <= 0.25 for stability).
    out = img_bchw.clone()
    for _ in range(iterations):
        # Compute gradients in 4 directions
        dn = F.pad(out, (0, 0, 0, 1), mode='reflect')[:, :, 1:, :] - out  # North
        ds = F.pad(out, (0, 0, 1, 0), mode='reflect')[:, :, :-1, :] - out  # South — shift down
        de = F.pad(out, (0, 1, 0, 0), mode='reflect')[:, :, :, 1:] - out  # East
        dw = F.pad(out, (1, 0, 0, 0), mode='reflect')[:, :, :, :-1] - out  # West

        # Fix: ensure all gradient tensors match spatial dimensions
        B, C, H, W = out.shape
        dn = dn[:, :, :H, :W]
        ds = ds[:, :, :H, :W]
        de = de[:, :, :H, :W]
        dw = dw[:, :, :H, :W]

        # Perona-Malik conductance (exponential)
        cn = torch.exp(-(dn / kappa) ** 2)
        cs = torch.exp(-(ds / kappa) ** 2)
        ce = torch.exp(-(de / kappa) ** 2)
        cw = torch.exp(-(dw / kappa) ** 2)

        out = out + gamma * (cn * dn + cs * ds + ce * de + cw * dw)

    return out.clamp_(0, 1)


# ============================================================================
# Edge Blur (Sobel edge mask + selective Gaussian)
# ============================================================================

def _sobel_edge_mask(img_bchw, threshold, dilation):
    # Compute gradient magnitude via Sobel, normalize, threshold, and dilate.
    # Returns a soft mask [B, 1, H, W] where 1 = edge region.
    B, C, H, W = img_bchw.shape
    # Convert to grayscale for edge detection
    gray = img_bchw.mean(dim=1, keepdim=True)  # [B, 1, H, W]

    # Sobel kernels
    sobel_x = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]],
                           device=img_bchw.device, dtype=img_bchw.dtype).view(1, 1, 3, 3)
    sobel_y = torch.tensor([[-1, -2, -1], [0, 0, 0], [1, 2, 1]],
                           device=img_bchw.device, dtype=img_bchw.dtype).view(1, 1, 3, 3)

    padded = F.pad(gray, (1, 1, 1, 1), mode='reflect')
    gx = F.conv2d(padded, sobel_x)
    gy = F.conv2d(padded, sobel_y)
    magnitude = torch.sqrt(gx ** 2 + gy ** 2)

    # Normalize to [0, 1] per image
    mag_max = magnitude.flatten(1).max(dim=1)[0].view(B, 1, 1, 1).clamp(min=1e-6)
    magnitude = magnitude / mag_max

    # Threshold — create a soft mask via sigmoid around the threshold
    # steepness=20 gives a fairly sharp but smooth transition
    mask = torch.sigmoid((magnitude - threshold) * 20)

    # Dilate the mask — expand edge regions using max pooling
    if dilation > 0:
        dil_size = 2 * dilation + 1
        mask = F.max_pool2d(mask, kernel_size=dil_size, stride=1,
                           padding=dilation)

    return mask


def _edge_blur(img_bchw, sigma, strength, radius):
    # Apply Gaussian blur only at detected hard edges.
    # radius controls edge detection sensitivity (threshold) and dilation.
    blurred = _gaussian_blur(img_bchw, sigma)

    # threshold: lower radius = more sensitive (detects more edges)
    # Map radius 0.1-10 → threshold 0.02-0.5
    threshold = 0.02 + (radius - 0.1) * (0.48 / 9.9)
    # Dilation: expand edge mask by 1-3 pixels based on sigma
    dilation = max(1, int(sigma * 0.5 + 0.5))

    mask = _sobel_edge_mask(img_bchw, threshold, dilation)
    # Apply strength to the mask
    mask = mask * abs(strength)

    # Blend: original where mask=0, blurred where mask=1
    if strength >= 0:
        out = img_bchw * (1 - mask) + blurred * mask
    else:
        # Negative strength → sharpen at edges (unsharp mask localized to edges)
        sharpened = 2.0 * img_bchw - blurred
        out = img_bchw * (1 - mask) + sharpened * mask

    return out


# ============================================================================
# Node Class
# ============================================================================

class RvImage_Soften(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Soften [Eclipse]",
            display_name="Image Soften",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description=(
                "Soften or sharpen an image. Positive strength softens, negative strength sharpens "
                "(unsharp mask). gaussian: uniform blur. bilateral: edge-preserving (smooths flat areas, keeps edges). "
                "wavelet: attenuates high-frequency detail while preserving color/structure. "
                "median: removes speckle/noise, preserves edges. "
                "anisotropic: Perona-Malik diffusion (iterative edge-preserving smoothing). "
                "edge_blur: targets hard edges only (Sobel detection), leaves flat areas untouched."
            ),
            inputs=[
                io.Image.Input("image", tooltip="Image to soften. Passes through on bypass."),
                io.Combo.Input("method",
                    options=["gaussian", "bilateral", "wavelet", "median", "anisotropic", "edge_blur"],
                    default="wavelet",
                    tooltip="gaussian: uniform blur. bilateral: edge-preserving. wavelet: frequency-band attenuation. "
                            "median: noise removal. anisotropic: Perona-Malik diffusion (smooths flat regions while preserving edges via gradient-based conductance). "
                            "edge_blur: Sobel-detected hard edges only — smooths burned/crispy edges while leaving flat areas untouched."),
                io.Float.Input("strength", default=0.5, min=-1.0, max=1.0, step=0.01,
                    tooltip="Positive = soften, negative = sharpen (unsharp mask). 0 = no change."),
                io.Float.Input("radius", default=1.5, min=0.1, max=10.0, step=0.1,
                    tooltip="Spatial radius/sigma (gaussian, bilateral, median), edge sensitivity (anisotropic), "
                            "or edge detection threshold (edge_blur — lower = detects more edges). "
                            "Higher = broader/softer blur. Lower = tighter/subtler effect. "
                            "Ignored by wavelet method."),
                io.Int.Input("iterations", default=8, min=1, max=50, step=1,
                    tooltip="Iterations for anisotropic diffusion. More = smoother. Ignored by other methods."),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, image, method, strength=0.5, radius=1.5, iterations=8):
        if strength == 0:
            return io.NodeOutput(image)

        device = model_management.get_torch_device()
        img = image.to(device).permute(0, 3, 1, 2).contiguous()  # BHWC → BCHW

        # Use abs(strength) for filter parameters — signed strength only matters in the blend
        abs_s = abs(strength)

        if method == "gaussian":
            sigma = radius * abs_s * 3  # Scale sigma by strength for intuitive control
            softened = _gaussian_blur(img, sigma)

        elif method == "bilateral":
            sigma_spatial = radius * 2
            sigma_color = 0.1 + (1.0 - abs_s) * 0.4  # Lower tolerance = more smoothing
            softened = _bilateral_filter(img, sigma_spatial, sigma_color)

        elif method == "wavelet":
            # Wavelet handles sign internally: positive attenuates, negative amplifies detail bands
            softened = _wavelet_soften(img, strength)

        elif method == "median":
            r = max(1, int(radius * abs_s * 2 + 0.5))
            softened = _median_filter(img, r)

        elif method == "anisotropic":
            kappa = 0.02 + (1.0 - abs_s) * 0.18  # Lower kappa = more aggressive edge filtering
            softened = _anisotropic_diffusion(img, iterations=iterations, kappa=kappa)

        elif method == "edge_blur":
            sigma = radius * 2  # Blur sigma for the Gaussian applied at edges
            softened = _edge_blur(img, sigma, strength, radius)

        else:
            softened = img

        # Blend: positive strength → soften, negative → unsharp mask
        # Formula: (1-s)*img + s*softened  →  at s=-0.5: 1.5*img - 0.5*softened (sharpening)
        # edge_blur handles its own blending internally (mask-based)
        if method not in ("wavelet", "edge_blur"):
            softened = (1.0 - strength) * img + strength * softened

        out = softened.permute(0, 2, 3, 1).contiguous()  # BCHW → BHWC
        return io.NodeOutput(out.cpu().float().clamp_(0, 1))
