#
# Image Upscale With Model — loads an upscale model and runs tiled inference on
# the input image in a single node. Combines the three built-in ComfyUI nodes
# (Load Upscale Model + Upscale Image using Model + Upscale Image By) into one.
#
# Optionally rescales the model's native output to a target multiplier using a
# standard resampling filter (e.g. model is 4× but you only want 2× output).
#

import torch  # type: ignore
import comfy.utils  # type: ignore
import comfy.model_management  # type: ignore
import folder_paths  # type: ignore
from spandrel import ModelLoader, ImageModelDescriptor  # type: ignore

try:
    from spandrel_extra_arches import EXTRA_REGISTRY  # type: ignore
    from spandrel import MAIN_REGISTRY  # type: ignore

    MAIN_REGISTRY.add(*EXTRA_REGISTRY)
except Exception:
    pass

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import unwrap_value, flatten_images, was_input_batch, prepare_image_output

_LOG_PREFIX = "ImageUpscaleWithModel"

_RESAMPLE_OPTIONS = ["lanczos", "bicubic", "bilinear", "area", "nearest-exact"]





class RvImage_UpscaleWithModel(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Upscale With Model [Eclipse]",
            display_name="Image Upscale With Model",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
            description="Load an upscale model and apply it to the image in one node. "
            "Optionally rescale the output to a target multiplier using a standard "
            "resampling filter (e.g. use a 4× model but produce 2× output).",
            is_input_list=True,
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input(
                    "model_name",
                    options=folder_paths.get_filename_list("upscale_models"),
                    tooltip="Upscale model from models/upscale_models/. "
                    "Models run at their native scale (e.g. 4× for RealESRGAN x4).",
                ),
                io.Float.Input(
                    "upscale_by",
                    default=0.0,
                    min=0.0,
                    max=16.0,
                    step=0.25,
                    tooltip="Target output multiplier relative to the original input size. "
                    "0.0 = keep the model's native output (e.g. 4× for a 4× model). "
                    "Any other value rescales the model output to the exact target dimensions.",
                ),
                io.Combo.Input(
                    "resampling",
                    options=_RESAMPLE_OPTIONS,
                    default="lanczos",
                    tooltip="Resampling filter used for the optional post-model rescale step. "
                    "Only applied when upscale_by > 0 and target size differs from model output.",
                ),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, image, model_name, upscale_by, resampling) -> io.NodeOutput:
        model_name = unwrap_value(model_name)
        upscale_by = unwrap_value(upscale_by, 0.0)
        resampling = unwrap_value(resampling, "lanczos")

        flat_image = flatten_images(image)
        if not flat_image:
            raise ValueError("Upscale model: No images provided in input.")

        was_batch = was_input_batch(image)

        # --- Load model ---
        model_path = folder_paths.get_full_path_or_raise("upscale_models", model_name)
        sd = comfy.utils.load_torch_file(model_path, safe_load=True)
        if "module.layers.0.residual_group.blocks.0.norm1.weight" in sd:
            sd = comfy.utils.state_dict_prefix_replace(sd, {"module.": ""})
        upscale_model = ModelLoader().load_from_state_dict(sd).eval()
        if not isinstance(upscale_model, ImageModelDescriptor):
            raise ValueError(
                "Upscale model must be a single-image model (ImageModelDescriptor)."
            )

        device = comfy.model_management.get_torch_device()
        upscale_model.to(device)

        upscaled_list = []
        try:
            for idx, img in enumerate(flat_image):
                # --- Run tiled model inference ---
                memory_required = comfy.model_management.module_size(
                    upscale_model.model
                )
                memory_required += (
                    (512 * 512 * 3)
                    * img.element_size()
                    * max(upscale_model.scale, 1.0)
                    * 384.0
                )
                memory_required += img.nelement() * img.element_size()
                comfy.model_management.free_memory(memory_required, device)

                in_img = img.movedim(-1, -3).to(device)  # [1, H, W, C] → [1, C, H, W]

                tile = 512
                overlap = 32
                output_device = comfy.model_management.intermediate_device()
                oom = True
                s = None
                while oom:
                    try:
                        steps = in_img.shape[0] * comfy.utils.get_tiled_scale_steps(
                            in_img.shape[3],
                            in_img.shape[2],
                            tile_x=tile,
                            tile_y=tile,
                            overlap=overlap,
                        )
                        pbar = comfy.utils.ProgressBar(steps)
                        s = comfy.utils.tiled_scale(
                            in_img,
                            lambda a: upscale_model(a.float()),
                            tile_x=tile,
                            tile_y=tile,
                            overlap=overlap,
                            upscale_amount=upscale_model.scale,
                            pbar=pbar,
                            output_device=output_device,
                        )
                        oom = False
                    except Exception as e:
                        comfy.model_management.raise_non_oom(e)
                        tile //= 2
                        if tile < 128:
                            raise e

                if s is None:
                    raise RuntimeError(
                        "Upscaling failed: output tensor is uninitialized."
                    )

                # s is [1, C, H, W] → convert to [1, H, W, C]
                s = torch.clamp(s.movedim(-3, -1), min=0, max=1.0).to(
                    comfy.model_management.intermediate_dtype()
                )

                # --- Optional post-model rescale ---
                if upscale_by > 0.0:
                    H_in, W_in = img.shape[1], img.shape[2]
                    target_w = max(1, round(W_in * upscale_by))
                    target_h = max(1, round(H_in * upscale_by))
                    out_H, out_W = s.shape[1], s.shape[2]
                    if out_W != target_w or out_H != target_h:
                        s = comfy.utils.common_upscale(
                            s.movedim(-1, 1), target_w, target_h, resampling, "disabled"
                        ).movedim(
                            1, -1
                        )  # back to [1, H, W, C]

                upscaled_list.append(s)
        finally:
            upscale_model.to("cpu")

        merged_tensor = torch.cat(upscaled_list, dim=0)
        result = prepare_image_output(merged_tensor, was_batch)
        return io.NodeOutput(result)
