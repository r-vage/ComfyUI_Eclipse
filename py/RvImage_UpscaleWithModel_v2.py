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

_LOG_PREFIX = "ImageUpscaleWithModel"

_RESAMPLE_OPTIONS = ["nearest-exact", "bilinear", "area", "bicubic", "lanczos"]


class RvImage_UpscaleWithModel_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Upscale With Model v2 [Eclipse]",
            display_name="Image Upscale With Model v2",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            description="Load an upscale model and apply it to the image in one node. "
                        "Optionally rescale the output to a target multiplier using a standard "
                        "resampling filter (e.g. use a 4× model but produce 2× output).",
            inputs=[
                io.Image.Input("image"),
                io.Combo.Input("model_name",
                               options=["None"] + folder_paths.get_filename_list("upscale_models"),
                               default="None",
                               tooltip="Upscale model from models/upscale_models/. "
                                       "Models run at their native scale (e.g. 4× for RealESRGAN x4)."),
                io.Float.Input("upscale_by", default=0.0, min=0.0, max=16.0, step=0.25,
                               tooltip="Target output multiplier relative to the original input size. "
                                       "0.0 = keep the model's native output (e.g. 4× for a 4× model). "
                                       "Any other value rescales the model output to the exact target dimensions."),
                io.Combo.Input("resampling",
                               options=_RESAMPLE_OPTIONS,
                               default="lanczos",
                               tooltip="Resampling filter used for the optional post-model rescale step. "
                                       "Only applied when upscale_by > 0 and target size differs from model output."),
                io.Int.Input("resolution_steps", default=8, min=1, max=256, step=1,
                             tooltip="Round target width and height to the nearest multiple of this value. "
                                     "VAE compatibility requires multiples of 8; some models/architectures require 64."),
                io.Boolean.Input("sharpen_enabled", default=False,
                                 tooltip="Enable post-upscale Smart Sharpen filter (using bilateral filtering and contrast sharpness)."),
                io.Float.Input("sharpen_amount", default=5.0, min=0.0, max=25.0, step=0.5,
                               tooltip="Amount of sharpness enhancement to apply."),
                io.Float.Input("sharpen_ratio", default=0.5, min=0.0, max=1.0, step=0.1,
                               tooltip="Blending ratio between the sharpened image and bilateral-blurred image (1.0 = purely sharpened, 0.0 = purely blurred)."),
                io.Int.Input("noise_radius", default=7, min=1, max=25, step=1,
                             tooltip="Bilateral filter noise reduction radius. Set to 1 to disable noise reduction blur."),
                io.Float.Input("preserve_edges", default=0.75, min=0.0, max=1.0, step=0.05,
                               tooltip="Edge preservation threshold (higher = keep more sharp edges during noise reduction)."),
            ],
            outputs=[
                io.Image.Output("image"),
            ],
        )

    @classmethod
    def execute(cls, image, model_name, upscale_by, resampling, resolution_steps=8,
                sharpen_enabled=False, sharpen_amount=5.0, sharpen_ratio=0.5,
                noise_radius=7, preserve_edges=0.75) -> io.NodeOutput:
        resolution_steps = max(1, resolution_steps or 8)

        if model_name in (None, "None", ""):
            # We do the upscale without the model with the given values.
            if upscale_by > 0.0:
                H_in, W_in = image.shape[1], image.shape[2]
                target_w = max(1, round(W_in * upscale_by / resolution_steps) * resolution_steps)
                target_h = max(1, round(H_in * upscale_by / resolution_steps) * resolution_steps)
                if target_w != W_in or target_h != H_in:
                    s = comfy.utils.common_upscale(
                        image.movedim(-1, 1), target_w, target_h, resampling, "disabled"
                    ).movedim(1, -1)
                else:
                    s = image
            else:
                s = image
        else:
            # --- Load model ---
            model_path = folder_paths.get_full_path_or_raise("upscale_models", model_name)
            sd = comfy.utils.load_torch_file(model_path, safe_load=True)
            if "module.layers.0.residual_group.blocks.0.norm1.weight" in sd:
                sd = comfy.utils.state_dict_prefix_replace(sd, {"module.": ""})
            upscale_model = ModelLoader().load_from_state_dict(sd).eval()
            if not isinstance(upscale_model, ImageModelDescriptor):
                raise ValueError("Upscale model must be a single-image model (ImageModelDescriptor).")

            # --- Run tiled model inference ---
            device = comfy.model_management.get_torch_device()
            # Estimate memory: module weights + per-tile activations + output buffer
            memory_required = comfy.model_management.module_size(upscale_model.model)
            memory_required += (512 * 512 * 3) * image.element_size() * max(upscale_model.scale, 1.0) * 384.0
            memory_required += image.nelement() * image.element_size()
            comfy.model_management.free_memory(memory_required, device)

            upscale_model.to(device)
            in_img = image.movedim(-1, -3).to(device)  # [B, H, W, C] → [B, C, H, W]

            tile = 512
            overlap = 32
            output_device = comfy.model_management.intermediate_device()
            oom = True
            s = None
            try:
                while oom:
                    try:
                        steps = in_img.shape[0] * comfy.utils.get_tiled_scale_steps(
                            in_img.shape[3], in_img.shape[2],
                            tile_x=tile, tile_y=tile, overlap=overlap)
                        pbar = comfy.utils.ProgressBar(steps)
                        s = comfy.utils.tiled_scale(
                            in_img,
                            lambda a: upscale_model(a.float()),
                            tile_x=tile, tile_y=tile,
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
            finally:
                upscale_model.to("cpu")

            if s is None:
                raise RuntimeError("Upscaling failed: output tensor is uninitialized.")

            # s is [B, C, H, W] → convert to [B, H, W, C]
            s = torch.clamp(s.movedim(-3, -1), min=0, max=1.0).to(comfy.model_management.intermediate_dtype())

            # --- Optional post-model rescale ---
            if upscale_by > 0.0:
                H_in, W_in = image.shape[1], image.shape[2]
                target_w = max(1, round(W_in * upscale_by / resolution_steps) * resolution_steps)
                target_h = max(1, round(H_in * upscale_by / resolution_steps) * resolution_steps)
                out_H, out_W = s.shape[1], s.shape[2]
                if out_W != target_w or out_H != target_h:
                    log.msg(_LOG_PREFIX,
                            f"Rescaling model output {out_W}×{out_H} → {target_w}×{target_h} "
                            f"(upscale_by={upscale_by}, model native scale={upscale_model.scale}×, resolution_steps={resolution_steps})")
                    # common_upscale expects [B, C, H, W]
                    s = comfy.utils.common_upscale(
                        s.movedim(-1, 1), target_w, target_h, resampling, "disabled"
                    ).movedim(1, -1)  # back to [B, H, W, C]

        # --- Apply Smart Sharpen if enabled ---
        if sharpen_enabled:
            try:
                import kornia  # type: ignore
            except ImportError:
                raise ImportError(
                    "kornia is required for Smart Sharpen. Please install it in your ComfyUI environment: "
                    "/mnt/data/AI/comfy_env/bin/python -m pip install kornia"
                )
            import cv2 # type: ignore

            output = []
            p_edges = preserve_edges
            if p_edges > 0:
                p_edges = max(1 - p_edges, 0.05)

            for img in s:
                if noise_radius > 1:
                    sigma = 0.3 * ((noise_radius - 1) * 0.5 - 1) + 0.8
                    img_np = img.cpu().numpy()
                    blurred = cv2.bilateralFilter(img_np, noise_radius, p_edges, sigma)
                    blurred = torch.from_numpy(blurred).to(device=img.device, dtype=img.dtype)
                else:
                    blurred = img

                if sharpen_amount > 0:
                    sharpened = kornia.enhance.sharpness(img.permute(2, 0, 1), sharpen_amount).permute(1, 2, 0)
                else:
                    sharpened = img

                sharpened_img = sharpen_ratio * sharpened + (1 - sharpen_ratio) * blurred
                sharpened_img = torch.clamp(sharpened_img, 0, 1)
                output.append(sharpened_img)

            s = torch.stack(output)

        return io.NodeOutput(s)
