# Modified for ComfyUI_Eclipse (GPLv3).
import torch  # type: ignore
from collections import namedtuple
import numpy as np  # type: ignore
import nodes  # type: ignore
import comfy  # type: ignore
import comfy.samplers  # type: ignore
import comfy.model_management  # type: ignore
import time
from . import utils
from . import impact_sampling
import inspect
import logging

try:
    from comfy_extras import nodes_differential_diffusion  # type: ignore
except Exception:
    logging.warning(
        "\n#############################################\n[Eclipse Impact] ComfyUI is an outdated version.\n#############################################\n"
    )
    raise Exception("[Impact Pack] ComfyUI is an outdated version.")

SEG = namedtuple(
    "SEG",
    [
        "cropped_image",
        "cropped_mask",
        "confidence",
        "crop_region",
        "bbox",
        "label",
        "control_net_wrapper",
    ],
    defaults=[None],
)

ADDITIONAL_SCHEDULERS = [
    "AYS SDXL",
    "AYS SD1",
    "AYS SVD",
    "GITS[coeff=1.2]",
    "LTXV[default]",
    "OSS FLUX",
    "OSS Wan",
    "OSS Chroma",
]


def get_schedulers():
    return list(comfy.samplers.SCHEDULER_HANDLERS) + ADDITIONAL_SCHEDULERS


def crop_condition_mask(mask, image, crop_region):
    cond_scale = (mask.shape[1] / image.shape[1], mask.shape[2] / image.shape[2])
    mask_region = [round(v * cond_scale[i % 2]) for i, v in enumerate(crop_region)]
    return utils.crop_ndarray3(mask, mask_region)


def segs_scale_match(segs, target_shape):
    h = segs[0][0]
    w = segs[0][1]

    th = target_shape[1]
    tw = target_shape[2]

    if (h == th and w == tw) or h == 0 or w == 0:
        return segs

    rh = th / h
    rw = tw / w

    new_segs = []
    for seg in segs[1]:
        cropped_image = seg.cropped_image
        cropped_mask = seg.cropped_mask
        x1, y1, x2, y2 = seg.crop_region
        bbox = seg.bbox

        crop_region = int(x1 * rw), int(y1 * rh), int(x2 * rw), int(y2 * rh)
        new_w = crop_region[2] - crop_region[0]
        new_h = crop_region[3] - crop_region[1]

        if isinstance(cropped_mask, np.ndarray):
            cropped_mask = torch.from_numpy(cropped_mask)

        if isinstance(cropped_mask, torch.Tensor) and len(cropped_mask.shape) == 3:
            cropped_mask = torch.nn.functional.interpolate(
                cropped_mask.unsqueeze(0),
                size=(new_h, new_w),
                mode="bilinear",
                align_corners=False,
            )
            cropped_mask = cropped_mask.squeeze(0)
        else:
            cropped_mask = torch.nn.functional.interpolate(
                cropped_mask.unsqueeze(0).unsqueeze(0),
                size=(new_h, new_w),
                mode="bilinear",
                align_corners=False,
            )
            cropped_mask = cropped_mask.squeeze(0).squeeze(0).numpy()

        if cropped_image is not None:
            cropped_image = utils.tensor_resize(
                (
                    cropped_image
                    if isinstance(cropped_image, torch.Tensor)
                    else torch.from_numpy(cropped_image)
                ),
                new_w,
                new_h,
            )
            cropped_image = cropped_image.numpy()

        new_seg = SEG(
            cropped_image,
            cropped_mask,
            seg.confidence,
            crop_region,
            bbox,
            seg.label,
            seg.control_net_wrapper,
        )
        new_segs.append(new_seg)

    return (th, tw), new_segs


class IPAdapterWrapper:
    def __init__(
        self,
        ipadapter_pipe,
        weight,
        noise,
        weight_type,
        start_at,
        end_at,
        unfold_batch,
        weight_v2,
        reference_image,
        neg_image=None,
        prev_control_net=None,
        combine_embeds="concat",
    ):
        self.reference_image = reference_image
        self.ipadapter_pipe = ipadapter_pipe
        self.weight = weight
        self.weight_type = weight_type
        self.noise = noise
        self.start_at = start_at
        self.end_at = end_at
        self.unfold_batch = unfold_batch
        self.prev_control_net = prev_control_net
        self.weight_v2 = weight_v2
        self.image = reference_image
        self.neg_image = neg_image
        self.combine_embeds = combine_embeds

    def doit_ipadapter(self, model):
        cnet_image_list = [self.image]
        prev_cnet_images = []

        if "IPAdapterAdvanced" not in nodes.NODE_CLASS_MAPPINGS:
            if "IPAdapterApply" in nodes.NODE_CLASS_MAPPINGS:
                raise Exception("[ERROR] 'ComfyUI IPAdapter Plus' is outdated.")

            utils.try_install_custom_node(
                "https://github.com/cubiq/ComfyUI_IPAdapter_plus",
                "To use 'IPAdapterApplySEGS' node, 'ComfyUI IPAdapter Plus' extension is required.",
            )
            raise Exception(
                "[ERROR] To use IPAdapterApplySEGS, you need to install 'ComfyUI IPAdapter Plus'"
            )

        obj = nodes.NODE_CLASS_MAPPINGS["IPAdapterAdvanced"]

        ipadapter, _, clip_vision, insightface, lora_loader = self.ipadapter_pipe
        model = lora_loader(model)

        if self.prev_control_net is not None:
            model, prev_cnet_images = self.prev_control_net.doit_ipadapter(model)

        model = obj().apply_ipadapter(
            model=model,
            ipadapter=ipadapter,
            weight=self.weight,
            weight_type=self.weight_type,
            start_at=self.start_at,
            end_at=self.end_at,
            combine_embeds=self.combine_embeds,
            clip_vision=clip_vision,
            image=self.image,
            image_negative=self.neg_image,
            attn_mask=None,
            insightface=insightface,
            weight_faceidv2=self.weight_v2,
        )[0]

        cnet_image_list.extend(prev_cnet_images)

        return model, cnet_image_list

    def apply(self, positive, negative, image, mask=None, use_acn=False):
        if self.prev_control_net is not None:
            return self.prev_control_net.apply(
                positive, negative, image, mask, use_acn=use_acn
            )
        else:
            return positive, negative, []


class ControlNetWrapper:
    def __init__(
        self,
        control_net,
        strength,
        preprocessor,
        prev_control_net=None,
        original_size=None,
        crop_region=None,
        control_image=None,
    ):
        self.control_net = control_net
        self.strength = strength
        self.preprocessor = preprocessor
        self.prev_control_net = prev_control_net

        if (
            original_size is not None
            and crop_region is not None
            and control_image is not None
        ):
            self.control_image = utils.tensor_resize(
                control_image, original_size[1], original_size[0]
            )
            self.control_image = torch.tensor(
                utils.tensor_crop(self.control_image, crop_region)
            )
        else:
            self.control_image = None

    def apply(self, positive, negative, image, mask=None, use_acn=False):
        cnet_image_list = []
        prev_cnet_images = []

        if self.prev_control_net is not None:
            positive, negative, prev_cnet_images = self.prev_control_net.apply(
                positive, negative, image, mask, use_acn=use_acn
            )

        if self.control_image is not None:
            cnet_image = self.control_image
        elif self.preprocessor is not None:
            cnet_image = self.preprocessor.apply(image, mask)
        else:
            cnet_image = image

        cnet_image_list.extend(prev_cnet_images)
        cnet_image_list.append(cnet_image)

        if use_acn:
            if "ACN_AdvancedControlNetApply" in nodes.NODE_CLASS_MAPPINGS:
                acn = nodes.NODE_CLASS_MAPPINGS["ACN_AdvancedControlNetApply"]()
                positive, negative, _ = acn.apply_controlnet(
                    positive=positive,
                    negative=negative,
                    control_net=self.control_net,
                    image=cnet_image,
                    strength=self.strength,
                    start_percent=0.0,
                    end_percent=1.0,
                )
            else:
                utils.try_install_custom_node(
                    "https://github.com/BlenderNeko/ComfyUI_TiledKSampler",
                    "To use 'ControlNetWrapper' for AnimateDiff, 'ComfyUI-Advanced-ControlNet' extension is required.",
                )
                raise Exception("'ACN_AdvancedControlNetApply' node isn't installed.")
        else:
            positive = nodes.ControlNetApply().apply_controlnet(
                positive, self.control_net, cnet_image, self.strength
            )[0]

        return positive, negative, cnet_image_list

    def doit_ipadapter(self, model):
        if self.prev_control_net is not None:
            return self.prev_control_net.doit_ipadapter(model)
        else:
            return model, []


class ControlNetAdvancedWrapper:
    def __init__(
        self,
        control_net,
        strength,
        start_percent,
        end_percent,
        preprocessor,
        prev_control_net=None,
        original_size=None,
        crop_region=None,
        control_image=None,
        vae=None,
    ):
        self.control_net = control_net
        self.strength = strength
        self.preprocessor = preprocessor
        self.prev_control_net = prev_control_net
        self.start_percent = start_percent
        self.end_percent = end_percent
        self.vae = vae

        if (
            original_size is not None
            and crop_region is not None
            and control_image is not None
        ):
            self.control_image = utils.tensor_resize(
                control_image, original_size[1], original_size[0]
            )
            self.control_image = torch.tensor(
                utils.tensor_crop(self.control_image, crop_region)
            )
        else:
            self.control_image = None

    def doit_ipadapter(self, model):
        if self.prev_control_net is not None:
            return self.prev_control_net.doit_ipadapter(model)
        else:
            return model, []

    def apply(self, positive, negative, image, mask=None, use_acn=False):
        cnet_image_list = []
        prev_cnet_images = []

        if self.prev_control_net is not None:
            positive, negative, prev_cnet_images = self.prev_control_net.apply(
                positive, negative, image, mask
            )

        if self.control_image is not None:
            cnet_image = self.control_image
        elif self.preprocessor is not None:
            cnet_image = self.preprocessor.apply(image, mask)
        else:
            cnet_image = image

        cnet_image_list.extend(prev_cnet_images)
        cnet_image_list.append(cnet_image)

        if use_acn:
            if "ACN_AdvancedControlNetApply" in nodes.NODE_CLASS_MAPPINGS:
                acn = nodes.NODE_CLASS_MAPPINGS["ACN_AdvancedControlNetApply"]()
                positive, negative, _ = acn.apply_controlnet(
                    positive=positive,
                    negative=negative,
                    control_net=self.control_net,
                    image=cnet_image,
                    strength=self.strength,
                    start_percent=self.start_percent,
                    end_percent=self.end_percent,
                )
            else:
                utils.try_install_custom_node(
                    "https://github.com/BlenderNeko/ComfyUI_TiledKSampler",
                    "To use 'ControlNetAdvancedWrapper' for AnimateDiff, 'ComfyUI-Advanced-ControlNet' extension is required.",
                )
                raise Exception("'ACN_AdvancedControlNetApply' node isn't installed.")
        else:
            if self.vae is not None:
                apply_controlnet = nodes.ControlNetApplyAdvanced().apply_controlnet
                signature = inspect.signature(apply_controlnet)

                if "vae" in signature.parameters:
                    (
                        positive,
                        negative,
                    ) = nodes.ControlNetApplyAdvanced().apply_controlnet(
                        positive,
                        negative,
                        self.control_net,
                        cnet_image,
                        self.strength,
                        self.start_percent,
                        self.end_percent,
                        vae=self.vae,
                    )
                else:
                    logging.error(
                        "[Eclipse Impact] ERROR: The ComfyUI version is outdated. VAE cannot be used in ApplyControlNet."
                    )
                    raise Exception(
                        "[Eclipse Impact] ERROR: The ComfyUI version is outdated. VAE cannot be used in ApplyControlNet."
                    )
            else:
                positive, negative = nodes.ControlNetApplyAdvanced().apply_controlnet(
                    positive,
                    negative,
                    self.control_net,
                    cnet_image,
                    self.strength,
                    self.start_percent,
                    self.end_percent,
                )

        return positive, negative, cnet_image_list


def enhance_detail(
    image,
    model,
    clip,
    vae,
    guide_size,
    guide_size_for_bbox,
    max_size,
    bbox,
    seed,
    steps,
    cfg,
    sampler_name,
    scheduler,
    positive,
    negative,
    denoise,
    noise_mask,
    force_inpaint,
    wildcard_opt=None,
    wildcard_opt_concat_mode=None,
    detailer_hook=None,
    refiner_ratio=None,
    refiner_model=None,
    refiner_clip=None,
    refiner_positive=None,
    refiner_negative=None,
    control_net_wrapper=None,
    cycle=1,
    inpaint_model=False,
    noise_mask_feather=0,
    scheduler_func=None,
    vae_tiled_encode=False,
    vae_tiled_decode=False,
    preview_enabled=True,
):

    if noise_mask is not None:
        noise_mask = utils.tensor_gaussian_blur_mask(noise_mask, noise_mask_feather)
        noise_mask = noise_mask.squeeze(3)

        if (
            noise_mask_feather > 0
            and "denoise_mask_function" not in model.model_options
        ):
            model = nodes_differential_diffusion.DifferentialDiffusion().execute(model)[
                0
            ]

    if wildcard_opt is not None and wildcard_opt != "":
        logging.warning(
            "[Eclipse Impact] Wildcard features are ignored in this stripped version."
        )

    h = image.shape[1]
    w = image.shape[2]

    bbox_h = bbox[3] - bbox[1]
    bbox_w = bbox[2] - bbox[0]

    # Skip processing if the detected bbox is already larger than the guide_size
    if not force_inpaint and bbox_h >= guide_size and bbox_w >= guide_size:
        logging.info("Detailer: segment skip (enough big)")
        return None, None

    if guide_size_for_bbox:  # == "bbox"
        # Scale up based on the smaller dimension between width and height.
        upscale = guide_size / min(bbox_w, bbox_h)
    else:
        # for cropped_size
        upscale = guide_size / min(w, h)

    new_w = int(w * upscale)
    new_h = int(h * upscale)

    # safeguard
    if "aitemplate_keep_loaded" in model.model_options:
        max_size = min(4096, max_size)

    if new_w > max_size or new_h > max_size:
        upscale *= max_size / max(new_w, new_h)
        new_w = int(w * upscale)
        new_h = int(h * upscale)

    if not force_inpaint:
        if upscale <= 1.0:
            logging.info(
                f"Detailer: segment skip [determined upscale factor={upscale}]"
            )
            return None, None

        if new_w == 0 or new_h == 0:
            logging.info(f"Detailer: segment skip [zero size={new_w, new_h}]")
            return None, None
    else:
        if upscale <= 1.0 or new_w == 0 or new_h == 0:
            logging.info("Detailer: force inpaint")
            upscale = 1.0
            new_w = w
            new_h = h

    if detailer_hook is not None:
        new_w, new_h = detailer_hook.touch_scaled_size(new_w, new_h)

    logging.info(
        f"Detailer: segment upscale for ({bbox_w, bbox_h}) | crop region {w, h} x {upscale} -> {new_w, new_h}"
    )

    # upscale
    upscaled_image = utils.tensor_resize(image, new_w, new_h)

    if detailer_hook is not None:
        upscaled_image = detailer_hook.post_upscale(upscaled_image, noise_mask)

    cnet_pils = None
    if control_net_wrapper is not None:
        positive, negative, cnet_pils = control_net_wrapper.apply(
            positive, negative, upscaled_image, noise_mask
        )
        model, cnet_pils2 = control_net_wrapper.doit_ipadapter(model)
        if cnet_pils is None:
            cnet_pils = []
        if cnet_pils2 is not None:
            cnet_pils.extend(cnet_pils2)

    # prepare mask
    if detailer_hook is None or not detailer_hook.get_skip_sampling():
        if noise_mask is not None and inpaint_model:
            imc_encode = nodes.InpaintModelConditioning().encode
            if "noise_mask" in inspect.signature(imc_encode).parameters:
                positive, negative, latent_image = imc_encode(
                    positive,
                    negative,
                    upscaled_image,
                    vae,
                    mask=noise_mask,
                    noise_mask=True,
                )
            else:
                logging.warning("[Eclipse Impact] ComfyUI is an outdated version.")
                positive, negative, latent_image = imc_encode(
                    positive, negative, upscaled_image, vae, noise_mask
                )
        else:
            latent_image = utils.to_latent_image(
                upscaled_image, vae, vae_tiled_encode=vae_tiled_encode
            )
            if noise_mask is not None:
                latent_image["noise_mask"] = noise_mask

        if detailer_hook is not None:
            latent_image = detailer_hook.post_encode(latent_image)

        refined_latent = latent_image

        sampler_opt = None
        if detailer_hook is not None:
            sampler_opt = detailer_hook.get_custom_sampler()

        # ksampler
        for i in range(0, cycle):
            if detailer_hook is not None:
                if detailer_hook is not None:
                    detailer_hook.set_steps((i, cycle))

                refined_latent = detailer_hook.cycle_latent(refined_latent)

                (
                    model2,
                    seed2,
                    steps2,
                    cfg2,
                    sampler_name2,
                    scheduler2,
                    positive2,
                    negative2,
                    upscaled_latent2,
                    denoise2,
                ) = detailer_hook.pre_ksample(
                    model,
                    seed + i,
                    steps,
                    cfg,
                    sampler_name,
                    scheduler,
                    positive,
                    negative,
                    latent_image,
                    denoise,
                )
                noise, is_touched = detailer_hook.get_custom_noise(
                    seed + i,
                    torch.zeros(latent_image["samples"].size()),
                    is_touched=False,
                )
                if not is_touched:
                    noise = None
            else:
                (
                    model2,
                    seed2,
                    steps2,
                    cfg2,
                    sampler_name2,
                    scheduler2,
                    positive2,
                    negative2,
                    _,
                    denoise2,
                ) = (
                    model,
                    seed + i,
                    steps,
                    cfg,
                    sampler_name,
                    scheduler,
                    positive,
                    negative,
                    latent_image,
                    denoise,
                )
                noise = None

            refined_latent = impact_sampling.ksampler_wrapper(
                model2,
                seed2,
                steps2,
                cfg2,
                sampler_name2,
                scheduler2,
                positive2,
                negative2,
                refined_latent,
                denoise2,
                refiner_ratio,
                refiner_model,
                refiner_clip,
                refiner_positive,
                refiner_negative,
                noise=noise,
                scheduler_func=scheduler_func,
                sampler_opt=sampler_opt,
                preview_enabled=preview_enabled,
            )

        if detailer_hook is not None:
            refined_latent = detailer_hook.pre_decode(refined_latent)

        # non-latent downscale - latent downscale cause bad quality
        start = time.time()
        if vae_tiled_decode:
            (refined_image,) = nodes.VAEDecodeTiled().decode(
                vae, refined_latent, 512
            )  # using default settings
            logging.info(
                f"[Eclipse Impact] vae decoded (tiled) in {time.time() - start:.1f}s"
            )
        else:
            try:
                refined_image = vae.decode(refined_latent["samples"])
            except Exception:
                # usually an out-of-memory exception from the decode, so try a tiled approach
                logging.warning(
                    f"[Eclipse Impact] failed after {time.time() - start:.1f}s, doing vae.decode_tiled 64..."
                )
                refined_image = vae.decode_tiled(
                    refined_latent["samples"],
                    tile_x=64,
                    tile_y=64,
                )
            logging.info(f"[Eclipse Impact] vae decoded in {time.time() - start:.1f}s")
    else:
        # skipped
        refined_image = upscaled_image

    if detailer_hook is not None:
        refined_image = detailer_hook.post_decode(refined_image)

    # downscale

    # workaround: support WAN as an i2i model
    if len(refined_image.shape) == 5:
        refined_image = refined_image.squeeze(0)

    refined_image = utils.tensor_resize(refined_image, w, h)

    # prevent mixing of device
    refined_image = refined_image.cpu()

    # don't convert to latent - latent break image
    # preserving pil is much better
    return refined_image, cnet_pils
