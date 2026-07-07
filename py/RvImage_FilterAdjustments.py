#
# Image Filter Adjustments — per-image brightness, contrast, saturation,
# sharpness, blur, gaussian blur, edge enhance, and detail enhance filters.
# Ported from WAS Node Suite (MIT licence, original author WASasquatch / ltdata).
#

import torch  # type: ignore
import comfy  # type: ignore
import comfy.utils  # type: ignore
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageEnhance, ImageFilter  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.image_helpers import tensor2pil, pil2tensor

_LOG_PREFIX = "ImageFilterAdjustments"


def _normalize_to_list(images) -> list:
    if isinstance(images, torch.Tensor):
        if images.dim() == 3:
            return [images.unsqueeze(0)]
        elif images.dim() == 4:
            return [images[i:i+1] for i in range(images.shape[0])]
    if isinstance(images, (list, tuple)):
        out = []
        for img in images:
            if isinstance(img, torch.Tensor):
                if img.dim() == 3:
                    out.append(img.unsqueeze(0))
                elif img.dim() == 4:
                    for j in range(img.shape[0]):
                        out.append(img[j:j+1])
        return out
    raise ValueError(f"Unsupported image input type: {type(images)}")


def _stack_and_force_size(tensors_list: list) -> torch.Tensor:
    if not tensors_list:
        return torch.empty((0, 64, 64, 3))
    first_tensor = tensors_list[0]
    target_h, target_w = first_tensor.shape[1], first_tensor.shape[2]
    adjusted_list = []
    for t in tensors_list:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_bchw = t.movedim(-1, 1)  # [1, C, H, W]
            t_resized = comfy.utils.common_upscale(t_bchw, target_w, target_h, "lanczos", "disabled")
            t = t_resized.movedim(1, -1)  # [1, H, W, C]
        adjusted_list.append(t)
    return torch.cat(adjusted_list, dim=0)


class RvImage_FilterAdjustments(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Filter Adjustments [Eclipse]",
            display_name="Image Filter Adjustments",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_FX.value,
            description="Apply brightness, contrast, saturation, sharpness, blur, "
                        "gaussian blur, edge enhance, and detail enhance filters to an image.",
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
                io.Boolean.Input(
                    "per_frame",
                    default=True,
                    tooltip="Process one frame at a time (safe for large batches, avoids OOM). Disable to process all frames in parallel — faster but uses more memory.",
                ),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, image, brightness, contrast, saturation, sharpness,
                blur, gaussian_blur, edge_enhance, detail_enhance, per_frame=True):
        is_list_input = isinstance(image, (list, tuple))
        image_list = _normalize_to_list(image)
        if not image_list:
            raise ValueError("Filter Adjustments: No images provided in input.")

        # PIL ops are inherently per-frame; skip entirely if nothing is requested.
        needs_pil = (
            saturation != 1.0
            or sharpness != 1.0
            or blur > 0
            or gaussian_blur > 0.0
            or edge_enhance > 0.0
            or detail_enhance
        )

        def process_frame(frame):
            pil_image = tensor2pil(frame)
            if saturation != 1.0:
                pil_image = ImageEnhance.Color(pil_image).enhance(saturation)
            if sharpness != 1.0:
                pil_image = ImageEnhance.Sharpness(pil_image).enhance(sharpness)
            if blur > 0:
                for _ in range(blur):
                    pil_image = pil_image.filter(ImageFilter.BLUR)
            if gaussian_blur > 0.0:
                pil_image = pil_image.filter(ImageFilter.GaussianBlur(radius=gaussian_blur))
            if edge_enhance > 0.0:
                enhanced = pil_image.filter(ImageFilter.EDGE_ENHANCE_MORE)
                mask = Image.new("L", pil_image.size, round(edge_enhance * 255))
                pil_image = Image.composite(enhanced, pil_image, mask)
            if detail_enhance:
                pil_image = pil_image.filter(ImageFilter.DETAIL)
            return pil2tensor(pil_image)

        processed_list = []
        for img in image_list:
            img = img.float()
            if brightness != 0.0:
                img = (img + brightness).clamp_(0.0, 1.0)
            if contrast != 1.0:
                img = (img * contrast).clamp_(0.0, 1.0)

            if not needs_pil:
                processed_list.append(img)
                continue

            frames = [img[j] for j in range(img.shape[0])]
            batch_size = len(frames)

            if per_frame or batch_size == 1:
                results = [process_frame(f) for f in frames]
            else:
                with ThreadPoolExecutor() as executor:
                    results = list(executor.map(process_frame, frames))

            processed_list.append(torch.cat(results, dim=0))

        if processed_list:
            out_image = _stack_and_force_size(processed_list)
        else:
            out_image = torch.empty((0, 64, 64, 3))

        return io.NodeOutput(out_image)
