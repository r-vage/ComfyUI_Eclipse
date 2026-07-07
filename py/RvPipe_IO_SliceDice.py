import os
import torch  # type: ignore
import comfy.utils  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images, cat_and_fit_images, prepare_image_output

_LOG_PREFIX = "PipeSlicer"


class RvPipe_IO_SliceDice(io.ComfyNode):
    # Slice and dice any connected inputs based on incoming indices.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Slicer & Dice [Eclipse]",
            display_name="IO Slice & Dice",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            description=(
                "Slice and dice any connected inputs (images, latents, conditionings, texts, filenames) "
                "based on the incoming list of indices. Also passes the indices downstream."
            ),
            inputs=[
                io.Custom("LIST").Input("indices", tooltip="Confirmed selected indices to slice by."),
                io.Image.Input("image", tooltip="Optional raw image batch.", optional=True),
                io.Image.Input("image_list", tooltip="Optional list of individual images.", optional=True),
                io.Latent.Input("latents", tooltip="Optional latent batch.", optional=True),
                io.Conditioning.Input("pos_cond", tooltip="Optional positive conditioning.", optional=True),
                io.Conditioning.Input("neg_cond", tooltip="Optional negative conditioning.", optional=True),
                io.String.Input("pos_txt", force_input=True, tooltip="Optional positive text.", optional=True),
                io.String.Input("neg_txt", force_input=True, tooltip="Optional negative text.", optional=True),
                io.String.Input("filenames", force_input=True, tooltip="Optional filename list.", optional=True),
            ],
            outputs=[
                io.Custom("LIST").Output(
                    "indices",
                    tooltip="Passed-through selection indices.",
                ),
                io.Image.Output(
                    "image", tooltip="Sliced image batch [N,H,W,C]."
                ),
                io.Image.Output(
                    "image_list", is_output_list=True, tooltip="Sliced list of individual images."
                ),
                io.Latent.Output(
                    "latents", is_output_list=True, tooltip="Sliced latent batch."
                ),
                io.Conditioning.Output(
                    "pos_cond", is_output_list=True, tooltip="Sliced positive conditioning."
                ),
                io.Conditioning.Output(
                    "neg_cond", is_output_list=True, tooltip="Sliced negative conditioning."
                ),
                io.String.Output(
                    "pos_txt", is_output_list=True, tooltip="Sliced positive text."
                ),
                io.String.Output(
                    "neg_txt", is_output_list=True, tooltip="Sliced negative text."
                ),
                io.String.Output(
                    "filenames", is_output_list=True, tooltip="Sliced filename list."
                ),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(
        cls,
        indices,
        image=None,
        image_list=None,
        latents=None,
        pos_cond=None,
        neg_cond=None,
        pos_txt=None,
        neg_txt=None,
        filenames=None,
    ):
        # try:
        #     with open("/mnt/data/AI/custom_nodes/comfyui_eclipse/slicer_debug.log", "a") as f:
        #         f.write(f"\n--- execute call ---\n")
        #         f.write(f"indices: {indices} (type: {type(indices)})\n")
        #         f.write(f"image is None: {image is None}\n")
        #         if image is not None:
        #             if isinstance(image, list):
        #                 f.write(f"  image list len: {len(image)}\n")
        #                 for idx, img in enumerate(image):
        #                     if isinstance(img, torch.Tensor):
        #                         f.write(f"    image[{idx}] shape: {img.shape}\n")
        #             elif isinstance(image, torch.Tensor):
        #                 f.write(f"  image tensor shape: {image.shape}\n")
        #         
        #         f.write(f"image_list is None: {image_list is None}\n")
        #         if image_list is not None:
        #             if isinstance(image_list, list):
        #                 f.write(f"  image_list len: {len(image_list)}\n")
        #                 for idx, img in enumerate(image_list):
        #                     if isinstance(img, torch.Tensor):
        #                         f.write(f"    image_list[{idx}] shape: {img.shape}\n")
        #             elif isinstance(image_list, torch.Tensor):
        #                 f.write(f"  image_list tensor shape: {image_list.shape}\n")
        #         
        #         f.write(f"latents is None: {latents is None}\n")
        #         if latents is not None:
        #             if isinstance(latents, list):
        #                 f.write(f"  latents len: {len(latents)}\n")
        #                 for idx, lat in enumerate(latents):
        #                     if isinstance(lat, dict) and "samples" in lat:
        #                         f.write(f"    latents[{idx}]['samples'] shape: {lat['samples'].shape}\n")
        #                     elif isinstance(lat, list):
        #                         f.write(f"    latents[{idx}] list len: {len(lat)}\n")
        #                         for jdx, subl in enumerate(lat):
        #                             if isinstance(subl, dict) and "samples" in subl:
        #                                 f.write(f"      latents[{idx}][{jdx}]['samples'] shape: {subl['samples'].shape}\n")
        #             elif isinstance(latents, dict) and "samples" in latents:
        #                 f.write(f"  latents dict samples shape: {latents['samples'].shape}\n")
        # 
        #         f.write(f"pos_cond is None: {pos_cond is None}\n")
        #         if pos_cond is not None:
        #             f.write(f"  pos_cond len: {len(pos_cond)}\n")
        #             f.write(f"  pos_cond type: {type(pos_cond)}\n")
        # 
        #         f.write(f"neg_cond is None: {neg_cond is None}\n")
        #         if neg_cond is not None:
        #             f.write(f"  neg_cond len: {len(neg_cond)}\n")
        # 
        #         f.write(f"pos_txt is None: {pos_txt is None}\n")
        #         if pos_txt is not None:
        #             f.write(f"  pos_txt: {pos_txt}\n")
        # 
        #         f.write(f"neg_txt is None: {neg_txt is None}\n")
        #         if neg_txt is not None:
        #             f.write(f"  neg_txt: {neg_txt}\n")
        # 
        #         f.write(f"filenames is None: {filenames is None}\n")
        #         if filenames is not None:
        #             f.write(f"  filenames: {filenames}\n")
        # except Exception as log_err:
        #     pass

        def unwrap_indices(val):
            if isinstance(val, list):
                if len(val) == 1 and isinstance(val[0], (list, str)):
                    return val[0]
                return val
            return val

        def parse_indices(val):
            if val is None:
                return []
            if isinstance(val, list):
                flat = []
                def _flatten(x):
                    if isinstance(x, list):
                        for item in x:
                            _flatten(item)
                    else:
                        flat.append(x)
                _flatten(val)
                res = []
                for item in flat:
                    try:
                        res.append(int(item))
                    except (ValueError, TypeError):
                        pass
                return res
            if isinstance(val, int):
                return [val]
            if isinstance(val, float):
                return [int(val)]
            if isinstance(val, str):
                import json
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return parse_indices(parsed)
                except Exception:
                    pass
                res = []
                for part in val.replace("\n", ",").split(","):
                    try:
                        res.append(int(part.strip()))
                    except (ValueError, TypeError):
                        pass
                return res
            if hasattr(val, "tolist"):
                try:
                    lst = val.tolist()
                    return parse_indices(lst)
                except Exception:
                    pass
            return []

        def flatten_latents(lat):
            if lat is None:
                return None
            flat = []
            def _process(item):
                if isinstance(item, (list, tuple)):
                    for sub in item:
                        _process(sub)
                elif isinstance(item, dict) and "samples" in item:
                    flat.append(item)
                elif item is not None:
                    flat.append(item)
            _process(lat)
            return flat

        def flatten_conditioning(cond):
            if cond is None:
                return None
            flat = []
            def _process(item):
                if isinstance(item, list) and len(item) > 0:
                    first = item[0]
                    if isinstance(first, (tuple, list)) and len(first) == 2 and isinstance(first[0], torch.Tensor):
                        flat.append(item)
                        return
                    for sub in item:
                        _process(sub)
                elif item is not None:
                    flat.append(item)
            _process(cond)
            return flat

        def flatten_texts(txt):
            if txt is None:
                return None
            flat = []
            def _process(item):
                if isinstance(item, (list, tuple)):
                    for sub in item:
                        _process(sub)
                elif isinstance(item, str):
                    flat.append(item)
                elif item is not None:
                    flat.append(item)
            _process(txt)
            return flat

        raw_indices = unwrap_indices(indices)
        parsed_indices = parse_indices(raw_indices)

        if not parsed_indices:
            log.msg(
                _LOG_PREFIX,
                "No valid indices found — passing empty/none outputs.",
            )
            return io.NodeOutput(
                parsed_indices, None, [None], [None], [None], [None], [None], [None], [None]
            )

        # Calculate total batch size from all connected inputs to determine the expansion factor.
        sizes = []
        def get_tensor_size(t):
            if isinstance(t, torch.Tensor):
                return t.shape[0]
            return 0

        if image is not None:
            if isinstance(image, list):
                sizes.append(sum(get_tensor_size(img) for img in image if img is not None))
            else:
                sizes.append(get_tensor_size(image))
                
        if image_list is not None:
            if isinstance(image_list, list):
                sizes.append(len(image_list))
            else:
                sizes.append(get_tensor_size(image_list))
                
        if latents is not None:
            if isinstance(latents, list):
                total = 0
                for lat in latents:
                    if isinstance(lat, dict) and "samples" in lat:
                        total += get_tensor_size(lat["samples"])
                    else:
                        total += 1
                sizes.append(total)
            elif isinstance(latents, dict) and "samples" in latents:
                sizes.append(get_tensor_size(latents["samples"]))
                
        if filenames is not None:
            if isinstance(filenames, list):
                sizes.append(len(filenames))

        total_batch_size = max(sizes) if sizes else 0

        def get_mapped_indices(B, indices, total_batch_size):
            if B <= 0:
                return []
            if B == len(indices) and indices == list(range(B)):
                return list(range(B))
            if total_batch_size > 0 and total_batch_size >= B:
                factor = total_batch_size // B
            else:
                factor = (max(indices) // B) + 1
            return [min(i // factor, B - 1) for i in indices]

        def slice_conditioning(cond_flat, indices):
            if not cond_flat:
                return None
            try:
                # Flatten the conditioning to individual tuples of batch size 1
                individual = []
                is_nested = len(cond_flat) > 0 and isinstance(cond_flat[0], list) and len(cond_flat[0]) > 0 and isinstance(cond_flat[0][0], (tuple, list))
                
                if is_nested:
                    # nested case: e.g. [ [ (tensor, dict), ... ] ]
                    for sub_list in cond_flat:
                        for c in sub_list:
                            if isinstance(c, (list, tuple)) and len(c) > 0 and isinstance(c[0], torch.Tensor):
                                tensor = c[0]
                                extra_dict = c[1] if len(c) > 1 else {}
                                B = tensor.shape[0]
                                for idx in range(B):
                                    sliced_tensor = tensor[idx:idx+1]
                                    sliced_extra_dict = {}
                                    if isinstance(extra_dict, dict):
                                        for k, v in extra_dict.items():
                                            if k == "pooled_output" and isinstance(v, torch.Tensor) and v.shape[0] == B:
                                                sliced_extra_dict[k] = v[idx:idx+1]
                                            else:
                                                sliced_extra_dict[k] = v
                                    individual.append((sliced_tensor, sliced_extra_dict))
                            else:
                                individual.append(c)
                    
                    B = len(individual)
                    mapped = get_mapped_indices(B, indices, total_batch_size)
                    selected = [individual[idx] for idx in mapped]
                    return [selected]
                else:
                    for c in cond_flat:
                        if isinstance(c, (list, tuple)) and len(c) > 0 and isinstance(c[0], torch.Tensor):
                            tensor = c[0]
                            extra_dict = c[1] if len(c) > 1 else {}
                            B = tensor.shape[0]
                            for idx in range(B):
                                sliced_tensor = tensor[idx:idx+1]
                                sliced_extra_dict = {}
                                if isinstance(extra_dict, dict):
                                    for k, v in extra_dict.items():
                                        if k == "pooled_output" and isinstance(v, torch.Tensor) and v.shape[0] == B:
                                            sliced_extra_dict[k] = v[idx:idx+1]
                                        else:
                                            sliced_extra_dict[k] = v
                                individual.append((sliced_tensor, sliced_extra_dict))
                        else:
                            individual.append(c)
                    
                    B = len(individual)
                    mapped = get_mapped_indices(B, indices, total_batch_size)
                    return [individual[idx] for idx in mapped]
            except Exception as e:
                log.error(_LOG_PREFIX, f"Error in slice_conditioning: {e}")
                return cond_flat

        # Helper to split latent dict of batch B into B latent dicts of batch 1
        def split_latent_dict(lat_dict):
            if lat_dict is None or "samples" not in lat_dict:
                return [lat_dict]
            samples = lat_dict["samples"]
            B = samples.shape[0]
            res = []
            for idx in range(B):
                item = {"samples": samples[idx:idx+1]}
                for k, v in lat_dict.items():
                    if k == "samples":
                        continue
                    if isinstance(v, torch.Tensor) and v.shape[0] == B:
                        item[k] = v[idx:idx+1]
                    else:
                        item[k] = v
                res.append(item)
            return res

        # Resolve image
        sliced_image = None
        if image is not None:
            flat_images = flatten_images(image)
            if flat_images:
                mapped = get_mapped_indices(len(flat_images), parsed_indices, total_batch_size)
                selected_imgs = [flat_images[idx] for idx in mapped]
                sliced_image = cat_and_fit_images(selected_imgs, log_prefix=_LOG_PREFIX)

        # Resolve image_list
        sliced_image_list = None
        if image_list is not None:
            flat_img_list = flatten_images(image_list)
            if flat_img_list:
                mapped = get_mapped_indices(len(flat_img_list), parsed_indices, total_batch_size)
                sliced_image_list = [flat_img_list[idx] for idx in mapped]

        # Cross-populate if only one of them is provided
        if sliced_image is not None and sliced_image_list is None:
            sliced_image_list = prepare_image_output(sliced_image, was_batch=False)
        elif sliced_image_list is not None and sliced_image is None:
            sliced_image = cat_and_fit_images(sliced_image_list, log_prefix=_LOG_PREFIX)

        # Resolve and slice latents
        sliced_latents = None
        if latents is not None:
            latents_flat = flatten_latents(latents)
            if latents_flat:
                individual_latents = []
                for lat in latents_flat:
                    individual_latents.extend(split_latent_dict(lat))
                mapped = get_mapped_indices(len(individual_latents), parsed_indices, total_batch_size)
                sliced_latents = [individual_latents[idx] for idx in mapped]

        # Resolve and slice conditioning
        sliced_pos_cond = slice_conditioning(flatten_conditioning(pos_cond), parsed_indices) if pos_cond is not None else None
        sliced_neg_cond = slice_conditioning(flatten_conditioning(neg_cond), parsed_indices) if neg_cond is not None else None

        # Resolve and slice text
        if pos_txt is not None:
            pos_txt_flat = flatten_texts(pos_txt)
            if pos_txt_flat is not None:
                mapped = get_mapped_indices(len(pos_txt_flat), parsed_indices, total_batch_size)
                sliced_pos_txt = [pos_txt_flat[idx] for idx in mapped]
            else:
                sliced_pos_txt = None
        else:
            sliced_pos_txt = None

        if neg_txt is not None:
            neg_txt_flat = flatten_texts(neg_txt)
            if neg_txt_flat is not None:
                mapped = get_mapped_indices(len(neg_txt_flat), parsed_indices, total_batch_size)
                sliced_neg_txt = [neg_txt_flat[idx] for idx in mapped]
            else:
                sliced_neg_txt = None
        else:
            sliced_neg_txt = None

        # Resolve and slice filenames
        if filenames is not None:
            filenames_flat = flatten_texts(filenames)
            if filenames_flat is not None:
                mapped = get_mapped_indices(len(filenames_flat), parsed_indices, total_batch_size)
                sliced_filenames = [filenames_flat[idx] for idx in mapped]
            else:
                sliced_filenames = None
        else:
            sliced_filenames = None

        # Handle replication if N > 1 and outputs have size 1
        N = len(parsed_indices)
        if N > 1:
            if (
                sliced_image is not None
                and isinstance(sliced_image, torch.Tensor)
                and sliced_image.shape[0] == 1
            ):
                sliced_image = sliced_image.repeat(N, 1, 1, 1)

            if (
                sliced_image_list is not None
                and isinstance(sliced_image_list, list)
                and len(sliced_image_list) == 1
            ):
                sliced_image_list = sliced_image_list * N

            if (
                sliced_latents is not None
                and isinstance(sliced_latents, list)
                and len(sliced_latents) == 1
                and isinstance(sliced_latents[0], dict)
            ):
                lat = sliced_latents[0]
                if "samples" in lat and lat["samples"].shape[0] == 1:
                    samples = lat["samples"].repeat(N, 1, 1, 1)
                    dup_latents = {"samples": samples}
                    for k, v in lat.items():
                        if k == "samples":
                            continue
                        if isinstance(v, torch.Tensor) and v.shape[0] == 1:
                            dup_latents[k] = v.repeat(N, *([1] * (v.dim() - 1)))
                        else:
                            dup_latents[k] = v
                    sliced_latents = [dup_latents] * N

        # Format output structures for lists (is_output_list=True)
        # 1. image_list
        image_list_out = [None]
        if sliced_image_list is not None:
            if isinstance(sliced_image_list, list):
                image_list_out = sliced_image_list
            elif isinstance(sliced_image_list, torch.Tensor):
                image_list_out = [sliced_image_list[i:i+1] for i in range(sliced_image_list.shape[0])]

        # 2. latents
        latents_out = [None]
        if sliced_latents is not None:
            if isinstance(sliced_latents, list):
                latents_out = sliced_latents
            elif isinstance(sliced_latents, dict) and "samples" in sliced_latents:
                latents_out = [sliced_latents]

        # 3. positive conditioning
        pos_cond_out = [None]
        if sliced_pos_cond is not None:
            if isinstance(sliced_pos_cond, list) and len(sliced_pos_cond) > 0:
                first = sliced_pos_cond[0]
                if isinstance(first, (tuple, list)) and len(first) == 2 and isinstance(first[0], torch.Tensor):
                    pos_cond_out = [sliced_pos_cond]
                else:
                    pos_cond_out = sliced_pos_cond
            else:
                pos_cond_out = [sliced_pos_cond]

        # 4. negative conditioning
        neg_cond_out = [None]
        if sliced_neg_cond is not None:
            if isinstance(sliced_neg_cond, list) and len(sliced_neg_cond) > 0:
                first = sliced_neg_cond[0]
                if isinstance(first, (tuple, list)) and len(first) == 2 and isinstance(first[0], torch.Tensor):
                    neg_cond_out = [sliced_neg_cond]
                else:
                    neg_cond_out = sliced_neg_cond
            else:
                neg_cond_out = [sliced_neg_cond]

        # 5. texts
        pos_txt_out = [None]
        if sliced_pos_txt is not None:
            pos_txt_out = sliced_pos_txt if isinstance(sliced_pos_txt, list) else [sliced_pos_txt]

        neg_txt_out = [None]
        if sliced_neg_txt is not None:
            neg_txt_out = sliced_neg_txt if isinstance(sliced_neg_txt, list) else [sliced_neg_txt]

        # 6. filenames
        filenames_out = [None]
        if sliced_filenames is not None:
            filenames_out = sliced_filenames if isinstance(sliced_filenames, list) else [sliced_filenames]

        return io.NodeOutput(
            parsed_indices,
            sliced_image,
            image_list_out,
            latents_out,
            pos_cond_out,
            neg_cond_out,
            pos_txt_out,
            neg_txt_out,
            filenames_out,
        )
