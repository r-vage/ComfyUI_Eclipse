# Modified for ComfyUI_Eclipse (GPLv3).
import os
import sys
import warnings
from io import BytesIO
import re

from . import core
from .core import SEG
from nodes import MAX_RESOLUTION  #type: ignore
from PIL import Image, ImageOps
import numpy as np  #type: ignore
import comfy.model_management  #type: ignore
import comfy.samplers  #type: ignore
from . import hooks
from . import utils
import inspect
import folder_paths  #type: ignore
import torch  #type: ignore
import nodes  #type: ignore
import logging

try:
    from comfy_extras import nodes_differential_diffusion  #type: ignore
except Exception:
    logging.warning("\n#############################################\n[Eclipse Impact] ComfyUI is an outdated version.\n#############################################\n")
    raise Exception("[Impact Pack] ComfyUI is an outdated version.")

warnings.filterwarnings('ignore', category=UserWarning, message='TypedStorage is deprecated')

class DetailerForEach:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "image": ("IMAGE", ),
                    "segs": ("SEGS", ),
                    "model": ("MODEL", {"tooltip": "If the `ImpactDummyInput` is connected to the model, the inference stage is skipped."}),
                    "clip": ("CLIP",),
                    "vae": ("VAE",),
                    "guide_size": ("FLOAT", {"default": 512, "min": 64, "max": nodes.MAX_RESOLUTION, "step": 8}),
                    "guide_size_for": ("BOOLEAN", {"default": True, "label_on": "bbox", "label_off": "crop_region"}),
                    "max_size": ("FLOAT", {"default": 1024, "min": 64, "max": nodes.MAX_RESOLUTION, "step": 8}),
                    "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                    "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
                    "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0}),
                    "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                    "scheduler": (core.get_schedulers(),),
                    "positive": ("CONDITIONING",),
                    "negative": ("CONDITIONING",),
                    "denoise": ("FLOAT", {"default": 0.5, "min": 0.0001, "max": 1.0, "step": 0.01}),
                    "feather": ("INT", {"default": 5, "min": 0, "max": 100, "step": 1}),
                    "noise_mask": ("BOOLEAN", {"default": True, "label_on": "enabled", "label_off": "disabled"}),
                    "force_inpaint": ("BOOLEAN", {"default": True, "label_on": "enabled", "label_off": "disabled"}),
                    "wildcard": ("STRING", {"multiline": True, "dynamicPrompts": False}),

                    "cycle": ("INT", {"default": 1, "min": 1, "max": 10, "step": 1}),
                   },
                "optional": {
                    "detailer_hook": ("DETAILER_HOOK",),
                    "inpaint_model": ("BOOLEAN", {"default": False, "label_on": "enabled", "label_off": "disabled"}),
                    "noise_mask_feather": ("INT", {"default": 20, "min": 0, "max": 100, "step": 1}),
                    "scheduler_func_opt": ("SCHEDULER_FUNC",),
                    "tiled_encode": ("BOOLEAN", {"default": False, "label_on": "enabled", "label_off": "disabled"}),
                    "tiled_decode": ("BOOLEAN", {"default": False, "label_on": "enabled", "label_off": "disabled"}),
                   }
                }

    RETURN_TYPES = ("IMAGE", )
    FUNCTION = "doit"

    CATEGORY = "ImpactPack/Detailer"

    DESCRIPTION = "It enhances details by inpainting each region within the detected area bundle (SEGS) after enlarging them based on the guide size."

    @staticmethod
    def get_core_module():
        return core

    @staticmethod
    def do_detail(image, segs, model, clip, vae, guide_size, guide_size_for_bbox, max_size, seed, steps, cfg, sampler_name, scheduler,
                  positive, negative, denoise, feather, noise_mask, force_inpaint, wildcard_opt=None, detailer_hook=None,
                  refiner_ratio=None, refiner_model=None, refiner_clip=None, refiner_positive=None, refiner_negative=None,
                  cycle=1, inpaint_model=False, noise_mask_feather=0, scheduler_func_opt=None, tiled_encode=False, tiled_decode=False):

        if len(image) > 1:
            raise Exception('[Impact Pack] ERROR: DetailerForEach does not allow image batches.\nPlease refer to https://github.com/ltdrdata/ComfyUI-extension-tutorials/blob/Main/ComfyUI-Impact-Pack/tutorial/batching-detailer.md for more information.')

        image = image.clone()
        enhanced_alpha_list = []
        enhanced_list = []
        cropped_list = []
        cnet_pil_list = []

        segs = core.segs_scale_match(segs, image.shape)
        new_segs = []

        wildcard_concat_mode = None
        wmode, wildcard_chooser = None, None

        if wmode in ['ASC', 'DSC', 'ASC-SIZE', 'DSC-SIZE']:
            if wmode == 'ASC':
                ordered_segs = sorted(segs[1], key=lambda x: (x.bbox[0], x.bbox[1]))
            elif wmode == 'DSC':
                ordered_segs = sorted(segs[1], key=lambda x: (x.bbox[0], x.bbox[1]), reverse=True)
            elif wmode == 'ASC-SIZE':
                ordered_segs = sorted(segs[1], key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]))

            else:   # wmode == 'DSC-SIZE'
                ordered_segs = sorted(segs[1], key=lambda x: (x.bbox[2]-x.bbox[0]) * (x.bbox[3]-x.bbox[1]), reverse=True)
        else:
            ordered_segs = segs[1]

        if not (isinstance(model, str) and model == "DUMMY") and noise_mask_feather > 0 and 'denoise_mask_function' not in model.model_options:
            model = nodes_differential_diffusion.DifferentialDiffusion().execute(model)[0]

        for i, seg in enumerate(ordered_segs):
            cropped_image = utils.crop_ndarray4(image.cpu().numpy(), seg.crop_region)  # Never use seg.cropped_image to handle overlapping area
            cropped_image = utils.to_tensor(cropped_image)
            mask = utils.to_tensor(seg.cropped_mask)
            mask = utils.tensor_gaussian_blur_mask(mask, feather)

            is_mask_all_zeros = (seg.cropped_mask == 0).all().item()
            if is_mask_all_zeros:
                logging.info("Detailer: segment skip [empty mask]")
                continue

            if noise_mask:
                cropped_mask = seg.cropped_mask
            else:
                cropped_mask = None

            seg_seed, wildcard_item = None, None

            seg_seed = seed + i if seg_seed is None else seg_seed

            if not isinstance(positive, str):
                cropped_positive = [
                    [condition, {
                        k: core.crop_condition_mask(v, image, seg.crop_region) if k == "mask" else v
                        for k, v in details.items()
                    }]
                    for condition, details in positive
                ]
            else:
                cropped_positive = positive

            if not isinstance(negative, str):
                cropped_negative = [
                    [condition, {
                        k: core.crop_condition_mask(v, image, seg.crop_region) if k == "mask" else v
                        for k, v in details.items()
                    }]
                    for condition, details in negative
                ]
            else:
                # Negative Conditioning is placeholder such as FLUX.1
                cropped_negative = negative

            orig_cropped_image = cropped_image.clone()
            if not (isinstance(model, str) and model == "DUMMY"):
                enhanced_image, cnet_pils = core.enhance_detail(cropped_image, model, clip, vae, guide_size, guide_size_for_bbox, max_size,
                                                                seg.bbox, seg_seed, steps, cfg, sampler_name, scheduler,
                                                                cropped_positive, cropped_negative, denoise, cropped_mask, force_inpaint,
                                                                wildcard_opt=wildcard_item, wildcard_opt_concat_mode=wildcard_concat_mode,
                                                                detailer_hook=detailer_hook,
                                                                refiner_ratio=refiner_ratio, refiner_model=refiner_model,
                                                                refiner_clip=refiner_clip, refiner_positive=refiner_positive,
                                                                refiner_negative=refiner_negative, control_net_wrapper=seg.control_net_wrapper,
                                                                cycle=cycle, inpaint_model=inpaint_model, noise_mask_feather=noise_mask_feather,
                                                                scheduler_func=scheduler_func_opt, vae_tiled_encode=tiled_encode,
                                                                vae_tiled_decode=tiled_decode)
            else:
                enhanced_image = cropped_image
                cnet_pils = None

            if cnet_pils is not None:
                cnet_pil_list.extend(cnet_pils)

            if enhanced_image is not None:
                # don't latent composite-> converting to latent caused poor quality
                # use image paste
                image = image.cpu()
                enhanced_image = enhanced_image.cpu()
                utils.tensor_paste(image, enhanced_image, (seg.crop_region[0], seg.crop_region[1]), mask)  # this code affecting to `cropped_image`.
                enhanced_list.append(enhanced_image)

                if detailer_hook is not None:
                    image = detailer_hook.post_paste(image)

            if enhanced_image is not None:
                # Convert enhanced_pil_alpha to RGBA mode
                enhanced_image_alpha = utils.tensor_convert_rgba(enhanced_image)
                new_seg_image = enhanced_image.numpy()  # alpha should not be applied to seg_image

                # Apply the mask
                mask = utils.tensor_resize(mask, *utils.tensor_get_size(enhanced_image))
                utils.tensor_putalpha(enhanced_image_alpha, mask)
                enhanced_alpha_list.append(enhanced_image_alpha)
            else:
                new_seg_image = None

            cropped_list.append(orig_cropped_image) # NOTE: Don't use `cropped_image`

            new_seg = SEG(new_seg_image, seg.cropped_mask, seg.confidence, seg.crop_region, seg.bbox, seg.label, seg.control_net_wrapper)
            new_segs.append(new_seg)

        image_tensor = utils.tensor_convert_rgb(image)

        cropped_list.sort(key=lambda x: x.shape, reverse=True)
        enhanced_list.sort(key=lambda x: x.shape, reverse=True)
        enhanced_alpha_list.sort(key=lambda x: x.shape, reverse=True)

        return image_tensor, cropped_list, enhanced_list, enhanced_alpha_list, cnet_pil_list, (segs[0], new_segs)

    def doit(self, image, segs, model, clip, vae, guide_size, guide_size_for, max_size, seed, steps, cfg, sampler_name,
             scheduler, positive, negative, denoise, feather, noise_mask, force_inpaint, wildcard, cycle=1,
             detailer_hook=None, inpaint_model=False, noise_mask_feather=0, scheduler_func_opt=None,
             tiled_encode=False, tiled_decode=False):

        enhanced_img, *_ = \
            DetailerForEach.do_detail(image, segs, model, clip, vae, guide_size, guide_size_for, max_size, seed, steps,
                                      cfg, sampler_name, scheduler, positive, negative, denoise, feather, noise_mask,
                                      force_inpaint, wildcard, detailer_hook,
                                      cycle=cycle, inpaint_model=inpaint_model, noise_mask_feather=noise_mask_feather,
                                      scheduler_func_opt=scheduler_func_opt, tiled_encode=tiled_encode, tiled_decode=tiled_decode)

        return (enhanced_img, )
