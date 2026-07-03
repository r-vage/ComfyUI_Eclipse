# KSampler (Pipe) [Eclipse]
# Eclipse KSampler using BASIC_PIPE and context generation data (PIPE).
# Accepts a basic pipe and optional gen_data, performs sampling, merges gen_data, and outputs the basic_pipe alongside the merged gen_data, latent, and image.

import random
from datetime import datetime
import torch
from comfy_api.latest import io  # type: ignore
import nodes  # type: ignore
import comfy.samplers  # type: ignore
import comfy.sample  # type: ignore
import comfy.utils  # type: ignore
import latent_preview  # type: ignore
from ..core import CATEGORY
from ..core.common import get_workflow_node
from ..core.logger import log
from typing import Any

_LOG_PREFIX = "Sampler (Pipe)"

# Same seed generator state for backend resolution
initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
eclipse_seed_random_state = random.getstate()
random.setstate(initial_random_state)


def new_random_seed():
    global eclipse_seed_random_state
    prev_random_state = random.getstate()
    random.setstate(eclipse_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    eclipse_seed_random_state = random.getstate()
    random.setstate(prev_random_state)
    return seed


class RvSampler_KSamplerPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Eclipse KSampler (Pipe) [Eclipse]",
            display_name="Eclipse KSampler (Pipe)",
            category=CATEGORY.MAIN.value + CATEGORY.SAMPLER.value,
            is_output_node=True,
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="The pipe dictionary from Smart Model Loader or other compatible pipe nodes."),
                io.Boolean.Input("allow_overwrite", default=False, label_on="yes", label_off="no", tooltip="When enabled, allows values from the pipe to take priority over/overwrite local widget settings."),
                io.Conditioning.Input("positive", tooltip="The positive conditioning (required, e.g. text prompt)."),
                io.Conditioning.Input("negative", tooltip="The negative conditioning (required, e.g. text prompt)."),
                io.Int.Input("steps", default=8, min=1, max=10000, tooltip="The number of steps used in the denoising process."),
                io.Float.Input("cfg", default=1.0, min=0.0, max=100.0, step=0.1, tooltip="The Classifier-Free Guidance scale."),
                io.Combo.Input("sampler_name", options=comfy.samplers.KSampler.SAMPLERS, default="res_multistep", tooltip="The sampling algorithm."),
                io.Combo.Input("scheduler", options=comfy.samplers.KSampler.SCHEDULERS, default="simple", tooltip="The scheduler algorithm."),
                io.Latent.Input("latent", optional=True, tooltip="Optional input latent to denoise. Either this or 'image' must be connected/provided in the pipe."),
                io.Image.Input("image", optional=True, tooltip="Optional input image to VAE-encode and denoise. Either this or 'latent' must be connected/provided in the pipe."),
                io.Float.Input("denoise", default=1.0, min=0.0, max=1.0, step=0.01, tooltip="The amount of denoising applied."),
                io.Boolean.Input("tiled_decode", default=False, label_on="enable", label_off="disable", tooltip="Enable tiled VAE decoding to save VRAM on large images."),
                io.Int.Input("tile_size", default=512, min=64, max=4096, step=32, tooltip="The size of the tiles used for tiled VAE decoding."),
                io.Combo.Input("preview_mode", options=["Preview", "None"], default="Preview", tooltip="Show the step-by-step rendering process during sampling and display the final decoded image at the end (Preview), or hide both (None) to keep the node layout clean."),
                io.Int.Input("seed", default=42, min=-3, max=2**64 - 1, control_after_generate=True, tooltip="The random seed used for creating the noise. Use -1 for random, -2 to increment, -3 to decrement."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe", tooltip="The updated pipe dictionary containing the model, clip, vae, latent, image, and sampler settings."),
                io.Latent.Output("latent", tooltip="The denoised latent."),
                io.Image.Output("image", tooltip="The decoded image."),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
            description="Eclipse KSampler using context pipe (PIPE). Performs sampling, VAE decoding, and updates the pipe with the final latent, image, and (optionally) sampler settings.",
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> Any:
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return new_random_seed()
        return seed

    @classmethod
    def execute(cls, pipe, seed, steps, cfg, sampler_name, scheduler, denoise, preview_mode, tiled_decode, tile_size, positive, negative, allow_overwrite=False, latent=None, image=None):
        if not isinstance(pipe, dict):
            raise ValueError("Eclipse KSampler (Pipe): The input 'pipe' is invalid or not connected.")

        model = pipe.get("model")
        vae = pipe.get("vae")

        if model is None:
            raise ValueError("Eclipse KSampler (Pipe): The input 'pipe' does not contain a 'model'.")
        if vae is None:
            raise ValueError("Eclipse KSampler (Pipe): The input 'pipe' does not contain a 'vae'.")

        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id

        # Resolve priority settings: pipe vs direct inputs
        # If allow_overwrite is True: pipe values take priority (if present in pipe).
        # If allow_overwrite is False: direct inputs/widgets on the node take priority.
        def resolve_val(key, direct_val):
            if key in pipe and pipe[key] is not None:
                pipe_val = pipe[key]
                if allow_overwrite:
                    return pipe_val
                else:
                    return direct_val if direct_val is not None else pipe_val
            return direct_val

        steps = resolve_val("steps", steps)
        cfg = resolve_val("cfg", cfg)
        sampler_name = resolve_val("sampler_name", sampler_name)
        scheduler = resolve_val("scheduler", scheduler)
        denoise = resolve_val("denoise", denoise)
        seed = resolve_val("seed", seed)

        # Resolve special seeds (-1, -2, -3) and save to metadata/workflow
        if seed in (-1, -2, -3):
            log.warning(_LOG_PREFIX, f'Got "{seed}" as passed seed. '
                        'This shouldn\'t happen when queueing from the ComfyUI frontend.')
            if seed in (-2, -3):
                log.warning(_LOG_PREFIX, f'Cannot {"increment" if seed == -2 else "decrement"} seed from '
                            'server, but will generate a new random seed.')

            original_seed = seed
            seed = new_random_seed()
            log.msg(_LOG_PREFIX, f'Server-generated random seed {seed} and saving to workflow.')

            if unique_id is not None:
                if extra_pnginfo is not None:
                    workflow_node = get_workflow_node(extra_pnginfo, unique_id)
                    if workflow_node is not None and 'widgets_values' in workflow_node:
                        for index, widget_value in enumerate(workflow_node['widgets_values']):
                            if widget_value == original_seed:
                                workflow_node['widgets_values'][index] = seed
                if prompt is not None:
                    prompt_node = prompt[str(unique_id)]
                    if prompt_node is not None and 'inputs' in prompt_node and 'seed' in prompt_node['inputs']:
                        prompt_node['inputs']['seed'] = seed

        # Resolve latent and image: direct input slots first, fallback to pipe
        resolved_image = image
        if resolved_image is None:
            resolved_image = pipe.get("image")

        resolved_latent = latent
        if resolved_latent is None:
            resolved_latent = pipe.get("latent")

        # Prefer image if one is connected/resolved, otherwise fallback to latent
        if resolved_image is not None:
            if isinstance(resolved_image, dict) and "image" in resolved_image:
                img_tensor = resolved_image["image"]
            else:
                img_tensor = resolved_image

            pixels = img_tensor[:, :, :, :3]
            if tiled_decode:
                overlap = max(16, tile_size // 8)
                t = vae.encode_tiled(pixels, tile_x=tile_size, tile_y=tile_size, overlap=overlap)
            else:
                t = vae.encode(pixels)
            resolved_latent = {"samples": t}
        elif resolved_latent is None:
            raise ValueError("Eclipse KSampler (Pipe): You must connect either a 'latent' or an 'image' input, or ensure one is provided in the pipe.")

        # 1. Perform sampling
        latent_samples = resolved_latent["samples"]
        latent_samples = comfy.sample.fix_empty_latent_channels(
            model, latent_samples,
            resolved_latent.get("downscale_ratio_spacial", None),
            resolved_latent.get("downscale_ratio_temporal", None)
        )

        batch_inds = resolved_latent["batch_index"] if "batch_index" in resolved_latent else None
        noise = comfy.sample.prepare_noise(latent_samples, seed, batch_inds)

        noise_mask = None
        if "noise_mask" in resolved_latent:
            noise_mask = resolved_latent["noise_mask"]

        # Conditional latent preview callback
        if preview_mode == "None":
            callback = None
        else:
            callback = latent_preview.prepare_callback(model, steps)

        disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
        samples = comfy.sample.sample(
            model, noise, steps, cfg, sampler_name, scheduler, positive, negative, latent_samples,
            denoise=denoise, disable_noise=False, start_step=None, last_step=None,
            force_full_denoise=False, noise_mask=noise_mask, callback=callback, disable_pbar=disable_pbar, seed=seed
        )

        latent_output = resolved_latent.copy()
        latent_output.pop("downscale_ratio_spacial", None)
        latent_output.pop("downscale_ratio_temporal", None)
        latent_output["samples"] = samples

        # 2. Perform VAE decode (either tiled or standard)
        if tiled_decode:
            decoder = nodes.VAEDecodeTiled()
            overlap = max(16, tile_size // 8)
            images = decoder.decode(vae=vae, samples=latent_output, tile_size=tile_size, overlap=overlap)[0]
        else:
            latent_val = latent_output["samples"]
            if latent_val.is_nested:
                latent_val = latent_val.unbind()[0]
            images = vae.decode(latent_val)
            if len(images.shape) == 5:
                images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])

        # 3. Handle UI render preview output
        if preview_mode == "None":
            ui_output = {"images": []}
        else:
            preview_node = nodes.PreviewImage()
            save_result = preview_node.save_images(images=images, prompt=prompt, extra_pnginfo=extra_pnginfo)
            ui_output = save_result.get("ui", {})

        # Extract dimensions from images tensor (shape: [batch, height, width, channels])
        height = 0
        width = 0
        if images is not None and len(images.shape) >= 3:
            height = int(images.shape[1])
            width = int(images.shape[2])

        # Construct updated output pipe (without mutating the input pipe directly)
        pipe_out = pipe.copy()
        pipe_out["latent"] = latent_output
        pipe_out["image"] = images
        pipe_out["width"] = width
        pipe_out["height"] = height
        pipe_out["positive"] = positive
        pipe_out["negative"] = negative

        if not allow_overwrite:
            pipe_out["steps"] = steps
            pipe_out["cfg"] = cfg
            pipe_out["sampler_name"] = sampler_name
            pipe_out["scheduler"] = scheduler
            pipe_out["denoise"] = denoise
            pipe_out["seed"] = seed

        # Return updated pipe, latent_output, images, and UI preview data
        return io.NodeOutput(pipe_out, latent_output, images, ui=ui_output)
