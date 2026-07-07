#
# Image Filter Adjustments Advanced — per-image adjustments including color,
# lighting, LUT, vignette, film grain, solarize, and chromatic aberration.
#

import torch  # type: ignore
import comfy  # type: ignore
import comfy.utils  # type: ignore
import folder_paths  # type: ignore
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageEnhance, ImageFilter  # type: ignore
import os

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.image_helpers import tensor2pil, pil2tensor, unwrap_value, flatten_images, was_input_batch, prepare_image_output

_LOG_PREFIX = "ImageFilterAdjustmentsAdvanced"

_LUT_CACHE = {}





def parse_cube(file_path):
    size = None
    rgb_list = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "LUT_3D_SIZE":
                size = int(parts[1])
            elif parts[0] in ("DOMAIN_MIN", "DOMAIN_MAX", "LUT_1D_SIZE", "TITLE"):
                continue
            elif len(parts) == 3:
                try:
                    rgb_list.append([float(x) for x in parts])
                except ValueError:
                    pass
    if size is None:
        raise ValueError(f"LUT_3D_SIZE not found in cube file: {file_path}")
    if len(rgb_list) != size**3:
        raise ValueError(
            f"Expected {size**3} data points in cube file, but got {len(rgb_list)}"
        )

    lut_tensor = torch.tensor(rgb_list, dtype=torch.float32)
    lut_tensor = lut_tensor.reshape(size, size, size, 3)
    lut_tensor = lut_tensor.permute(3, 0, 1, 2).unsqueeze(0)  # [1, 3, size, size, size]
    return lut_tensor


def load_lut_cached(lut_name):
    if lut_name in _LUT_CACHE:
        return _LUT_CACHE[lut_name]

    lut_path = folder_paths.get_full_path_or_raise("luts", lut_name)
    lut_tensor = parse_cube(lut_path)

    _LUT_CACHE[lut_name] = lut_tensor
    return lut_tensor


def rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    max_val, max_idx = torch.max(rgb, dim=-1)
    min_val, _ = torch.min(rgb, dim=-1)
    d = max_val - min_val

    eps = 1e-7
    d_safe = torch.where(
        d == 0.0, torch.tensor(eps, device=rgb.device, dtype=rgb.dtype), d
    )

    r_max = max_idx == 0
    g_max = max_idx == 1
    b_max = max_idx == 2

    h = torch.zeros_like(max_val)
    h[r_max] = (((g[r_max] - b[r_max]) / d_safe[r_max]) % 6) / 6.0
    h[g_max] = (((b[g_max] - r[g_max]) / d_safe[g_max]) + 2) / 6.0
    h[b_max] = (((r[b_max] - g[b_max]) / d_safe[b_max]) + 4) / 6.0

    s = torch.zeros_like(max_val)
    max_val_safe = torch.where(
        max_val == 0.0, torch.tensor(eps, device=rgb.device, dtype=rgb.dtype), max_val
    )
    s = d / max_val_safe

    v = max_val

    return torch.stack((h, s, v), dim=-1)


def hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    c = v * s
    x = c * (1.0 - torch.abs((h * 6.0) % 2.0 - 1.0))
    m = v - c

    h_mod = (h * 6.0).to(torch.int32) % 6

    r = torch.zeros_like(h)
    g = torch.zeros_like(h)
    b = torch.zeros_like(h)

    cond = h_mod == 0
    r[cond], g[cond], b[cond] = c[cond], x[cond], 0.0

    cond = h_mod == 1
    r[cond], g[cond], b[cond] = x[cond], c[cond], 0.0

    cond = h_mod == 2
    r[cond], g[cond], b[cond] = 0.0, c[cond], x[cond]

    cond = h_mod == 3
    r[cond], g[cond], b[cond] = 0.0, x[cond], c[cond]

    cond = h_mod == 4
    r[cond], g[cond], b[cond] = x[cond], 0.0, c[cond]

    cond = h_mod == 5
    r[cond], g[cond], b[cond] = c[cond], 0.0, x[cond]

    rgb = torch.stack((r + m, g + m, b + m), dim=-1)
    return rgb


def adjust_hue(image, hue_shift):
    if hue_shift == 0.0:
        return image
    hsv = rgb_to_hsv(image)
    hsv[..., 0] = (hsv[..., 0] + (hue_shift / 360.0)) % 1.0
    return hsv_to_rgb(hsv).clamp(0.0, 1.0)


def adjust_temperature_and_tint(image, temperature, tint):
    if temperature == 0.0 and tint == 0.0:
        return image

    r = image[..., 0]
    g = image[..., 1]
    b = image[..., 2]

    if temperature != 0.0:
        if temperature > 0.0:
            r = r * (1.0 + temperature)
            g = g * (1.0 + temperature * 0.4)
        else:
            b = b * (1.0 - temperature)

    if tint != 0.0:
        if tint > 0.0:
            r = r * (1.0 + tint * 0.1)
            b = b * (1.0 + tint * 0.1)
        else:
            g = g * (1.0 - tint * 0.1)

    return torch.stack((r, g, b), dim=-1).clamp(0.0, 1.0)


def apply_vignette(image, intensity, center_x, center_y):
    if intensity <= 0.0:
        return image

    B, H, W, C = image.shape
    device = image.device
    dtype = image.dtype

    y = torch.linspace(0, 1, H, device=device, dtype=dtype)
    x = torch.linspace(0, 1, W, device=device, dtype=dtype)
    grid_y, grid_x = torch.meshgrid(y, x, indexing="ij")

    dist = torch.sqrt((grid_x - center_x) ** 2 + (grid_y - center_y) ** 2)
    max_dist = torch.sqrt(
        torch.tensor(
            max(center_x, 1.0 - center_x) ** 2 + max(center_y, 1.0 - center_y) ** 2,
            device=device,
            dtype=dtype,
        )
    )
    dist = dist / max_dist

    mask = 1.0 - intensity * (dist**2)
    mask = torch.clamp(mask, 0.0, 1.0).view(1, H, W, 1)

    return image * mask


def apply_film_grain(image, strength, size, saturation):
    if strength <= 0.0:
        return image

    B, H, W, C = image.shape
    device = image.device
    dtype = image.dtype

    if size > 1.0:
        noise_h = max(2, int(H / size))
        noise_w = max(2, int(W / size))
    else:
        noise_h, noise_w = H, W

    gray_noise = torch.randn(B, noise_h, noise_w, 1, device=device, dtype=dtype)

    if saturation > 0.0:
        color_noise = torch.randn(B, noise_h, noise_w, 3, device=device, dtype=dtype)
        noise = torch.lerp(gray_noise, color_noise, saturation)
    else:
        noise = gray_noise.expand(-1, -1, -1, 3)

    if size > 1.0:
        noise = noise.movedim(-1, 1)
        noise = torch.nn.functional.interpolate(
            noise, size=(H, W), mode="bilinear", align_corners=False
        )
        noise = noise.movedim(1, -1)

    lum = (image * torch.tensor([0.299, 0.587, 0.114], device=device, dtype=dtype)).sum(
        dim=-1, keepdim=True
    )
    grain_mask = 4.0 * lum * (1.0 - lum)

    grain = noise * strength * grain_mask
    return torch.clamp(image + grain, 0.0, 1.0)


def apply_solarize(image, threshold):
    if threshold <= 0.0:
        return image
    return torch.where(image < threshold, image, 1.0 - image)


def apply_chromatic_aberration(image, ca_amount):
    if ca_amount <= 0.0:
        return image

    B, H, W, C = image.shape
    device = image.device
    dtype = image.dtype

    img_bchw = image.movedim(-1, 1)

    y = torch.linspace(-1, 1, H, device=device, dtype=dtype)
    x = torch.linspace(-1, 1, W, device=device, dtype=dtype)
    grid_y, grid_x = torch.meshgrid(y, x, indexing="ij")
    grid = torch.stack((grid_x, grid_y), dim=-1).unsqueeze(0).expand(B, -1, -1, -1)

    shift = ca_amount * 0.02
    grid_r = grid * (1.0 - shift)
    out_r = torch.nn.functional.grid_sample(
        img_bchw[:, 0:1],
        grid_r,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )

    out_g = img_bchw[:, 1:2]

    grid_b = grid * (1.0 + shift)
    out_b = torch.nn.functional.grid_sample(
        img_bchw[:, 2:3],
        grid_b,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )

    out_img = torch.cat((out_r, out_g, out_b), dim=1).movedim(1, -1)
    return out_img


def apply_lut(image, lut_name, strength=1.0):
    if lut_name == "none" or not lut_name:
        return image

    lut_tensor = load_lut_cached(lut_name)
    B, H, W, C = image.shape
    device = image.device
    dtype = image.dtype

    lut = lut_tensor.to(device=device, dtype=dtype).expand(B, -1, -1, -1, -1)
    grid = image.clamp(0.0, 1.0) * 2.0 - 1.0
    grid = grid.unsqueeze(1)

    out = torch.nn.functional.grid_sample(
        lut, grid, mode="bilinear", padding_mode="border", align_corners=True
    )

    out = out.squeeze(2).permute(0, 2, 3, 1)

    if strength < 1.0:
        out = torch.lerp(image, out, strength)

    return out


class RvImage_FilterAdjustmentsAdvanced(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        # Register LUTs folder dynamically in folder_paths
        if "luts" not in folder_paths.folder_names_and_paths:
            luts_dir = os.path.join(folder_paths.models_dir, "luts")
            if not os.path.exists(luts_dir):
                os.makedirs(luts_dir, exist_ok=True)
            folder_paths.folder_names_and_paths["luts"] = ([luts_dir], {".cube"})

        try:
            luts = folder_paths.get_filename_list("luts")
            lut_list = ["none"] + sorted(luts)
        except Exception:
            lut_list = ["none"]

        return io.Schema(
            node_id="Image Filter Adjustments Advanced [Eclipse]",
            display_name="Image Filter Adjustments Advanced",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_FX.value,
            description="Apply brightness, contrast, saturation, sharpness, blur, "
            "white balance (temp/tint/hue), solarize, LUT, vignette, "
            "chromatic aberration, and film grain effects.",
            is_input_list=True,
            inputs=[
                io.Image.Input("image"),
                io.Float.Input(
                    "brightness",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Additive brightness offset. 0 = no change.",
                ),
                io.Float.Input(
                    "contrast",
                    default=1.0,
                    min=-1.0,
                    max=2.0,
                    step=0.01,
                    tooltip="Multiplicative contrast factor. 1 = no change.",
                ),
                io.Float.Input(
                    "saturation",
                    default=1.0,
                    min=0.0,
                    max=5.0,
                    step=0.01,
                    tooltip="Colour saturation factor. 1 = no change.",
                ),
                io.Float.Input(
                    "sharpness",
                    default=1.0,
                    min=-5.0,
                    max=5.0,
                    step=0.01,
                    tooltip="Sharpness factor. 1 = no change.",
                ),
                io.Int.Input(
                    "blur",
                    default=0,
                    min=0,
                    max=16,
                    step=1,
                    tooltip="Number of box-blur passes.",
                ),
                io.Float.Input(
                    "gaussian_blur",
                    default=0.0,
                    min=0.0,
                    max=1024.0,
                    step=0.1,
                    tooltip="Gaussian blur radius. 0 = disabled.",
                ),
                io.Float.Input(
                    "edge_enhance",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Edge enhancement blend strength. 0 = disabled.",
                ),
                io.Boolean.Input(
                    "detail_enhance",
                    default=False,
                    tooltip="Apply PIL DETAIL filter.",
                ),
                io.Float.Input(
                    "hue_shift",
                    default=0.0,
                    min=-180.0,
                    max=180.0,
                    step=1.0,
                    tooltip="Shift image hue in degrees. 0 = no change. Suggested range: -10.0 to 10.0 for minor corrections.",
                ),
                io.Float.Input(
                    "color_temp",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Color temperature warmth shift. Negative = cool (blue), Positive = warm (yellow). Suggested range: -0.2 to 0.2.",
                ),
                io.Float.Input(
                    "color_tint",
                    default=0.0,
                    min=-1.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Color tint shift. Negative = green, Positive = magenta. Suggested range: -0.1 to 0.1.",
                ),
                io.Float.Input(
                    "vignette_intensity",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Vignette edge darkening strength. 0 = disabled. Suggested values: 0.1 - 0.4.",
                ),
                io.Float.Input(
                    "vignette_center_x",
                    default=0.5,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Vignette center X coordinate.",
                ),
                io.Float.Input(
                    "vignette_center_y",
                    default=0.5,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Vignette center Y coordinate.",
                ),
                io.Float.Input(
                    "grain_strength",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Film grain overlay strength. 0 = disabled. Suggested values: 0.02 - 0.05 for subtle grain, 0.10 for heavy grain.",
                ),
                io.Float.Input(
                    "grain_size",
                    default=1.0,
                    min=1.0,
                    max=10.0,
                    step=0.1,
                    tooltip="Film grain pixel clump size. Suggested values: 1.0 - 2.5.",
                ),
                io.Float.Input(
                    "grain_saturation",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Film grain color saturation. 0 = monochrome.",
                ),
                io.Float.Input(
                    "solarize_threshold",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Solarize color inversion threshold. 0 = disabled. Suggested values: 0.5 - 0.8.",
                ),
                io.Float.Input(
                    "chromatic_aberration",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="Chromatic aberration displacement strength. 0 = disabled. Suggested values: 0.01 - 0.05 for subtle realism, 0.1+ for strong styling.",
                ),
                io.Combo.Input(
                    "lut_name",
                    options=lut_list,
                    default="none",
                    tooltip="3D Look-Up Table (LUT) file (.cube) from models/luts/.",
                ),
                io.Float.Input(
                    "lut_strength",
                    default=1.0,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="LUT blending opacity/strength.",
                ),
                io.Boolean.Input(
                    "per_frame",
                    default=True,
                    tooltip="Process PIL actions one frame at a time (safe for large batches, avoids OOM).",
                ),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
            ],
        )

    @classmethod
    def execute(
        cls,
        image,
        brightness,
        contrast,
        saturation,
        sharpness,
        blur,
        gaussian_blur,
        edge_enhance,
        detail_enhance,
        hue_shift,
        color_temp,
        color_tint,
        vignette_intensity,
        vignette_center_x,
        vignette_center_y,
        grain_strength,
        grain_size,
        grain_saturation,
        solarize_threshold,
        chromatic_aberration,
        lut_name,
        lut_strength,
        per_frame=True,
    ):
        brightness = unwrap_value(brightness, 0.0)
        contrast = unwrap_value(contrast, 1.0)
        saturation = unwrap_value(saturation, 1.0)
        sharpness = unwrap_value(sharpness, 1.0)
        blur = unwrap_value(blur, 0)
        gaussian_blur = unwrap_value(gaussian_blur, 0.0)
        edge_enhance = unwrap_value(edge_enhance, 0.0)
        detail_enhance = unwrap_value(detail_enhance, False)
        hue_shift = unwrap_value(hue_shift, 0.0)
        color_temp = unwrap_value(color_temp, 0.0)
        color_tint = unwrap_value(color_tint, 0.0)
        vignette_intensity = unwrap_value(vignette_intensity, 0.0)
        vignette_center_x = unwrap_value(vignette_center_x, 0.5)
        vignette_center_y = unwrap_value(vignette_center_y, 0.5)
        grain_strength = unwrap_value(grain_strength, 0.0)
        grain_size = unwrap_value(grain_size, 1.0)
        grain_saturation = unwrap_value(grain_saturation, 0.0)
        solarize_threshold = unwrap_value(solarize_threshold, 0.0)
        chromatic_aberration = unwrap_value(chromatic_aberration, 0.0)
        lut_name = unwrap_value(lut_name, "none")
        lut_strength = unwrap_value(lut_strength, 1.0)
        per_frame = unwrap_value(per_frame, True)

        flat_image = flatten_images(image)
        if not flat_image:
            raise ValueError(
                "Filter Adjustments Advanced: No images provided in input."
            )

        was_batch = was_input_batch(image)

        # Determine if we need to do any PIL operations
        needs_pil = (
            saturation != 1.0
            or sharpness != 1.0
            or blur > 0
            or gaussian_blur > 0.0
            or edge_enhance > 0.0
            or detail_enhance
        )

        def process_pil_frame(frame):
            pil_image = tensor2pil(frame)
            if saturation != 1.0:
                pil_image = ImageEnhance.Color(pil_image).enhance(saturation)
            if sharpness != 1.0:
                pil_image = ImageEnhance.Sharpness(pil_image).enhance(sharpness)
            if blur > 0:
                for _ in range(blur):
                    pil_image = pil_image.filter(ImageFilter.BLUR)
            if gaussian_blur > 0.0:
                pil_image = pil_image.filter(
                    ImageFilter.GaussianBlur(radius=gaussian_blur)
                )
            if edge_enhance > 0.0:
                enhanced = pil_image.filter(ImageFilter.EDGE_ENHANCE_MORE)
                mask = Image.new("L", pil_image.size, round(edge_enhance * 255))
                pil_image = Image.composite(enhanced, pil_image, mask)
            if detail_enhance:
                pil_image = pil_image.filter(ImageFilter.DETAIL)
            return pil2tensor(pil_image)

        preprocessed = []
        for img in flat_image:
            img = img.float()
            if brightness != 0.0:
                img = (img + brightness).clamp_(0.0, 1.0)
            if contrast != 1.0:
                img = (img * contrast).clamp_(0.0, 1.0)
            preprocessed.append(img)

        # Step 2: PIL operations (if requested)
        if needs_pil:
            if per_frame or len(preprocessed) == 1:
                pil_results = [process_pil_frame(img[0]) for img in preprocessed]
            else:
                frames_to_process = [img[0] for img in preprocessed]
                with ThreadPoolExecutor() as executor:
                    pil_results = list(executor.map(process_pil_frame, frames_to_process))
            # Reconstruct the batch list
            preprocessed = pil_results

        # Step 3: Pure PyTorch effects
        # order: WB/Hue -> Solarize -> LUT -> Vignette -> CA -> Grain
        processed_list = []
        for img in preprocessed:
            img = adjust_temperature_and_tint(img, color_temp, color_tint)
            img = adjust_hue(img, hue_shift)
            img = apply_solarize(img, solarize_threshold)
            img = apply_lut(img, lut_name, lut_strength)
            img = apply_vignette(
                img, vignette_intensity, vignette_center_x, vignette_center_y
            )
            img = apply_chromatic_aberration(img, chromatic_aberration)
            img = apply_film_grain(img, grain_strength, grain_size, grain_saturation)
            processed_list.append(img)

        merged_tensor = torch.cat(processed_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)
