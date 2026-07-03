import numpy as np
import torch
from collections import namedtuple
import comfy.samplers
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..extern.impact.impact_pack import DetailerForEach
from ..extern.impact.core import SEG, get_schedulers
from ..extern.impact import utils as impact_utils

def fixed_segs_scale_match(segs, target_shape):
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
        bx1, by1, bx2, by2 = seg.bbox

        # Fixed coordinate scaling ratios!
        crop_region = int(x1*rw), int(y1*rh), int(x2*rw), int(y2*rh)
        bbox = int(bx1*rw), int(by1*rh), int(bx2*rw), int(by2*rh)
        new_w = crop_region[2] - crop_region[0]
        new_h = crop_region[3] - crop_region[1]

        if isinstance(cropped_mask, np.ndarray):
            cropped_mask = torch.from_numpy(cropped_mask)

        # PyTorch interpolations
        if isinstance(cropped_mask, torch.Tensor) and len(cropped_mask.shape) == 3:
            cropped_mask = torch.nn.functional.interpolate(cropped_mask.unsqueeze(0), size=(new_h, new_w), mode='bilinear', align_corners=False)
            cropped_mask = cropped_mask.squeeze(0)
        else:
            cropped_mask = torch.nn.functional.interpolate(cropped_mask.unsqueeze(0).unsqueeze(0), size=(new_h, new_w), mode='bilinear', align_corners=False)
            cropped_mask = cropped_mask.squeeze(0).squeeze(0).numpy()

        if cropped_image is not None:
            image_tensor = cropped_image if isinstance(cropped_image, torch.Tensor) else torch.from_numpy(cropped_image)
            cropped_image = impact_utils.tensor_resize(image_tensor, new_w, new_h)
            cropped_image = cropped_image.numpy()

        new_seg = SEG(cropped_image, cropped_mask, seg.confidence, crop_region, bbox, seg.label, seg.control_net_wrapper)
        new_segs.append(new_seg)

    return (th, tw), new_segs

class RvDetailer_ForEachPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Detailer (SEGS/pipe) [Eclipse]",
            display_name="Detailer (SEGS/pipe)",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            is_input_list=True,
            inputs=[
                io.Image.Input("image", tooltip="Input image frames (can be batch or list)."),
                io.Custom("SEGS").Input("segs", tooltip="SEGS data to detail."),
                io.Float.Input("guide_size", default=512.0, min=64.0, max=8192.0, step=8.0, tooltip="Guide size for resizing cropped areas."),
                io.Boolean.Input("guide_size_for", default=True, label_on="bbox", label_off="crop_region", tooltip="Whether guide size is for bbox or crop region."),
                io.Float.Input("max_size", default=1024.0, min=64.0, max=8192.0, step=8.0, tooltip="Maximum size of the cropped area."),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff, tooltip="Seed for sampler."),
                io.Int.Input("steps", default=20, min=1, max=10000, tooltip="Steps for sampler."),
                io.Float.Input("cfg", default=8.0, min=0.0, max=100.0, step=0.1, tooltip="CFG for sampler."),
                io.Combo.Input("sampler_name", options=comfy.samplers.KSampler.SAMPLERS, default="euler", tooltip="Sampler name."),
                io.Combo.Input("scheduler", options=get_schedulers(), default="normal", tooltip="Scheduler name."),
                io.Float.Input("denoise", default=0.5, min=0.0001, max=1.0, step=0.01, tooltip="Denoise level."),
                io.Int.Input("feather", default=5, min=0, max=100, tooltip="Feather level for blending."),
                io.Boolean.Input("noise_mask", default=True, label_on="enabled", label_off="disabled", tooltip="Use noise mask for sampling."),
                io.Boolean.Input("force_inpaint", default=True, label_on="enabled", label_off="disabled", tooltip="Force inpainting even if mask is empty."),
                io.Custom("BASIC_PIPE").Input("basic_pipe", tooltip="Basic pipe containing model, clip, vae, positive, negative."),
                io.Float.Input("refiner_ratio", default=0.2, min=0.0, max=1.0, step=0.01, tooltip="Refiner ratio."),
                io.Int.Input("cycle", default=1, min=1, max=10, tooltip="Detailer cycle count."),
                # Optional inputs
                io.Custom("DETAILER_HOOK").Input("detailer_hook", optional=True, tooltip="Detailer hook."),
                io.Custom("BASIC_PIPE").Input("refiner_basic_pipe_opt", optional=True, tooltip="Refiner basic pipe."),
                io.Boolean.Input("inpaint_model", default=False, label_on="enabled", label_off="disabled", tooltip="Force using inpaint model."),
                io.Int.Input("noise_mask_feather", default=20, min=0, max=100, tooltip="Feather level for noise mask."),
                io.Custom("SCHEDULER_FUNC").Input("scheduler_func_opt", optional=True, tooltip="Scheduler function option."),
                io.Boolean.Input("tiled_encode", default=False, label_on="enabled", label_off="disabled", tooltip="Use tiled encode."),
                io.Boolean.Input("tiled_decode", default=False, label_on="enabled", label_off="disabled", tooltip="Use tiled decode."),
            ],
            outputs=[
                io.Image.Output("image", is_output_list=True),
                io.Custom("SEGS").Output("segs", is_output_list=True),
                io.Custom("BASIC_PIPE").Output("basic_pipe", is_output_list=True),
                io.Image.Output("cnet_images", is_output_list=True),
            ]
        )

    @classmethod
    def execute(cls, image, segs, guide_size, guide_size_for, max_size, seed, steps, cfg, sampler_name, scheduler,
                denoise, feather, noise_mask, force_inpaint, basic_pipe, refiner_ratio, cycle,
                detailer_hook=None, refiner_basic_pipe_opt=None, inpaint_model=False, noise_mask_feather=20,
                scheduler_func_opt=None, tiled_encode=False, tiled_decode=False):
        
        # Extract widgets/options (widgets are passed as lists when is_input_list=True)
        guide_size = guide_size[0]
        guide_size_for = guide_size_for[0]
        max_size = max_size[0]
        seed = seed[0]
        steps = steps[0]
        cfg = cfg[0]
        sampler_name = sampler_name[0]
        scheduler = scheduler[0]
        denoise = denoise[0]
        feather = feather[0]
        noise_mask = noise_mask[0]
        force_inpaint = force_inpaint[0]
        refiner_ratio = refiner_ratio[0]
        cycle = cycle[0]
        inpaint_model = inpaint_model[0]  # type: ignore
        noise_mask_feather = noise_mask_feather[0]  # type: ignore
        tiled_encode = tiled_encode[0]  # type: ignore
        tiled_decode = tiled_decode[0]  # type: ignore
        
        detailer_hook = detailer_hook[0] if detailer_hook else None
        refiner_basic_pipe_opt = refiner_basic_pipe_opt[0] if refiner_basic_pipe_opt else None
        scheduler_func_opt = scheduler_func_opt[0] if scheduler_func_opt else None
        
        segs_val = segs[0]
        basic_pipe_val = basic_pipe[0]

        # Convert image input to a flat list of single-frame image tensors (1, H, W, 3)
        image_list = []
        for img in image:
            if img.ndim == 3:
                image_list.append(img.unsqueeze(0))
            else:
                for j in range(img.shape[0]):
                    image_list.append(img[j:j+1])
                    
        # Extract model elements
        model, clip, vae, positive, negative = basic_pipe_val
        if refiner_basic_pipe_opt is None:
            refiner_model, refiner_clip, refiner_positive, refiner_negative = None, None, None, None
        else:
            refiner_model, refiner_clip, _, refiner_positive, refiner_negative = refiner_basic_pipe_opt
 
        enhanced_images = []
        cnet_images_all = []
        detailed_segs_list = []
        
        # Process each image in the list
        for idx, img_i in enumerate(image_list):
            # Scale segs to match target image shape using fixed coordinate scaling!
            # This ensures that DetailerForEach's internal segs_scale_match becomes a no-op.
            scaled_segs = fixed_segs_scale_match(segs_val, img_i.shape)
            
            # Filter segments by their batch index
            filtered_segs_list = []
            for seg in scaled_segs[1]:
                seg_batch_index = 0
                if hasattr(seg, 'control_net_wrapper') and hasattr(seg.control_net_wrapper, 'batch_index'):
                    seg_batch_index = seg.control_net_wrapper.batch_index
                
                if seg_batch_index == idx:
                    filtered_segs_list.append(seg)
                    
            filtered_segs = (scaled_segs[0], filtered_segs_list)
            
            # Execute DetailerForEach (wildcard_opt is set to None/ignored)
            enhanced_img, cropped, cropped_enhanced, cropped_enhanced_alpha, cnet_pil_list, new_segs = \
                DetailerForEach.do_detail(img_i, filtered_segs, model, clip, vae, guide_size, guide_size_for, max_size, seed, steps, cfg,
                                          sampler_name, scheduler, positive, negative, denoise, feather, noise_mask, force_inpaint,
                                          wildcard_opt=None, detailer_hook=detailer_hook,
                                          refiner_ratio=refiner_ratio, refiner_model=refiner_model,
                                          refiner_clip=refiner_clip, refiner_positive=refiner_positive,
                                          refiner_negative=refiner_negative,
                                          cycle=cycle, inpaint_model=inpaint_model, noise_mask_feather=noise_mask_feather,
                                          scheduler_func_opt=scheduler_func_opt, tiled_encode=tiled_encode,
                                          tiled_decode=tiled_decode)
            
            enhanced_images.append(enhanced_img)
            detailed_segs_list.extend(new_segs[1])
            cnet_images_all.extend(cnet_pil_list)

        # Convert cnet_images PIL to PyTorch if they are PIL
        cnet_tensors = []
        for cimg in cnet_images_all:
            if isinstance(cimg, np.ndarray):
                cnet_tensors.append(torch.from_numpy(cimg))
            elif isinstance(cimg, torch.Tensor):
                cnet_tensors.append(cimg)
            else:
                cnet_tensors.append(impact_utils.to_tensor(cimg))

        # We return lists because of is_input_list=True
        return io.NodeOutput(enhanced_images, [(segs_val[0], detailed_segs_list)], [basic_pipe_val], cnet_tensors)
