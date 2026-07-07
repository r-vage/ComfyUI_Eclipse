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
from ..core.image_helpers import tensor2pil, pil2tensor, unwrap_value, flatten_images, was_input_batch, prepare_image_output

_LOG_PREFIX = "ImageFilterAdjustments"





class RvImage_FilterAdjustments(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Filter Adjustments [Eclipse]",
            display_name="Image Filter Adjustments",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_FX.value,
            description="Apply brightness, contrast, saturation, sharpness, blur, "
            "gaussian blur, edge enhance, and detail enhance filters to an image.",
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
                io.Boolean.Input(
                    "per_frame",
                    default=True,
                    tooltip="Process one frame at a time (safe for large batches, avoids OOM). Disable to process all frames in parallel — faster but uses more memory.",
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
        per_frame = unwrap_value(per_frame, True)

        flat_image = flatten_images(image)
        if not flat_image:
            raise ValueError("Filter Adjustments: No images provided in input.")

        was_batch = was_input_batch(image)

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

        processed_list = []
        if per_frame or len(flat_image) == 1:
            for img in flat_image:
                img = img.float()
                if brightness != 0.0:
                    img = (img + brightness).clamp_(0.0, 1.0)
                if contrast != 1.0:
                    img = (img * contrast).clamp_(0.0, 1.0)

                if not needs_pil:
                    processed_list.append(img)
                    continue

                processed_list.append(process_frame(img[0]))
        else:
            preprocessed = []
            for img in flat_image:
                img = img.float()
                if brightness != 0.0:
                    img = (img + brightness).clamp_(0.0, 1.0)
                if contrast != 1.0:
                    img = (img * contrast).clamp_(0.0, 1.0)
                preprocessed.append(img)

            if not needs_pil:
                processed_list = preprocessed
            else:
                frames_to_process = [img[0] for img in preprocessed]
                with ThreadPoolExecutor() as executor:
                    processed_list = list(executor.map(process_frame, frames_to_process))

        merged_tensor = torch.cat(processed_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)
