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


def _apply_sharpen(s, sharpen_enabled, sharpen_amount, sharpen_ratio, noise_radius, preserve_edges):
    if not sharpen_enabled:
        return s
    try:
        import kornia  # type: ignore
    except ImportError:
        raise ImportError(
            "kornia is required for Smart Sharpen. Please install it in your ComfyUI environment: "
            "/mnt/data/AI/comfy_env/bin/python -m pip install kornia"
        )
    import cv2  # type: ignore

    p_edges = preserve_edges
    if p_edges > 0:
        p_edges = max(1 - p_edges, 0.05)

    output = []
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

    return torch.stack(output)


def _stack_and_force_size(tensors_list: list, resampling: str = "lanczos") -> torch.Tensor:
    if not tensors_list:
        return torch.empty((0, 64, 64, 3))
    first_tensor = tensors_list[0]
    target_h, target_w = first_tensor.shape[1], first_tensor.shape[2]
    adjusted_list = []
    for t in tensors_list:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            t_bchw = t.movedim(-1, 1)  # [1, C, H, W]
            t_resized = comfy.utils.common_upscale(t_bchw, target_w, target_h, resampling, "disabled")
            t = t_resized.movedim(1, -1)  # [1, H, W, C]
        adjusted_list.append(t)
    return torch.cat(adjusted_list, dim=0)


class RvImage_UpscaleWithModel_v2(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Upscale With Model v2 [Eclipse]",
            display_name="Image Upscale w/wo Model",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_TRANSFORMS.value,
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

        # --- Normalize list ---
        is_list_input = isinstance(image, (list, tuple))
        image_list = _normalize_to_list(image)
        if not image_list:
            raise ValueError("Upscale model v2: No images provided in input.")

        upscaled_list = []

        if model_name in (None, "None", ""):
            # We do the upscale without the model with the given values.
            for img in image_list:
                if upscale_by > 0.0:
                    H_in, W_in = img.shape[1], img.shape[2]
                    target_w = max(1, round(W_in * upscale_by / resolution_steps) * resolution_steps)
                    target_h = max(1, round(H_in * upscale_by / resolution_steps) * resolution_steps)
                    if target_w != W_in or target_h != H_in:
                        s = comfy.utils.common_upscale(
                            img.movedim(-1, 1), target_w, target_h, resampling, "disabled"
                        ).movedim(1, -1)
                    else:
                        s = img
                else:
                    s = img

                s = _apply_sharpen(s, sharpen_enabled, sharpen_amount, sharpen_ratio, noise_radius, preserve_edges)
                upscaled_list.append(s)
        else:
            # --- Load model ---
            model_path = folder_paths.get_full_path_or_raise("upscale_models", model_name)
            sd = comfy.utils.load_torch_file(model_path, safe_load=True)
            if "module.layers.0.residual_group.blocks.0.norm1.weight" in sd:
                sd = comfy.utils.state_dict_prefix_replace(sd, {"module.": ""})
            upscale_model = ModelLoader().load_from_state_dict(sd).eval()
            if not isinstance(upscale_model, ImageModelDescriptor):
                raise ValueError("Upscale model must be a single-image model (ImageModelDescriptor).")

            device = comfy.model_management.get_torch_device()
            upscale_model.to(device)

            try:
                for img in image_list:
                    # --- Run tiled model inference ---
                    # Estimate memory: module weights + per-tile activations + output buffer
                    memory_required = comfy.model_management.module_size(upscale_model.model)
                    memory_required += (512 * 512 * 3) * img.element_size() * max(upscale_model.scale, 1.0) * 384.0
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

                    if s is None:
                        raise RuntimeError("Upscaling failed: output tensor is uninitialized.")

                    # s is [1, C, H, W] → convert to [1, H, W, C]
                    s = torch.clamp(s.movedim(-3, -1), min=0, max=1.0).to(comfy.model_management.intermediate_dtype())

                    # --- Optional post-model rescale ---
                    if upscale_by > 0.0:
                        H_in, W_in = img.shape[1], img.shape[2]
                        target_w = max(1, round(W_in * upscale_by / resolution_steps) * resolution_steps)
                        target_h = max(1, round(H_in * upscale_by / resolution_steps) * resolution_steps)
                        out_H, out_W = s.shape[1], s.shape[2]
                        if out_W != target_w or out_H != target_h:
                            s = comfy.utils.common_upscale(
                                s.movedim(-1, 1), target_w, target_h, resampling, "disabled"
                            ).movedim(1, -1)  # back to [1, H, W, C]

                    s = _apply_sharpen(s, sharpen_enabled, sharpen_amount, sharpen_ratio, noise_radius, preserve_edges)
                    upscaled_list.append(s)
            finally:
                upscale_model.to("cpu")

        if upscaled_list:
            out_image = _stack_and_force_size(upscaled_list, resampling)
        else:
            out_image = torch.empty((0, 64, 64, 3))

        return io.NodeOutput(out_image)
