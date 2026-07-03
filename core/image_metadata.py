# Image metadata extraction utilities for ComfyUI image nodes.
# Supports: Auto1111, EasyDiffusion, InvokeAI (modern & legacy), NovelAI,
#           ComfyUI (with workflow-based prompt tracing), DrawThings
#
# credits to comfyanonymous for the initial code of the image load node
# credits to https://github.com/Jordach/comfy-plasma for the initial code of the metadata extraction

import json
import re

from typing import Any, Dict
from xml.dom import minidom

from .common import SCHEDULERS_ANY
from .logger import log

_LOG_PREFIX = "ImageMetadata"


# ============================================================================
# Mapping constants
# ============================================================================

EASYDIFFUSION_MAPPING_A = {
    "prompt": "Prompt",
    "negative_prompt": "Negative Prompt",
    "seed": "Seed",
    "use_stable_diffusion_model": "Stable Diffusion model",
    "clip_skip": "Clip Skip",
    "use_vae_model": "VAE model",
    "sampler_name": "Sampler",
    "width": "Width",
    "height": "Height",
    "num_inference_steps": "Steps",
    "guidance_scale": "Guidance Scale",
}

EASYDIFFUSION_MAPPING_B = {
    "prompt": "prompt",
    "negative_prompt": "negative_prompt",
    "seed": "seed",
    "use_stable_diffusion_model": "use_stable_diffusion_model",
    "clip_skip": "clip_skip",
    "use_vae_model": "use_vae_model",
    "sampler_name": "sampler_name",
    "width": "width",
    "height": "height",
    "num_inference_steps": "num_inference_steps",
    "guidance_scale": "guidance_scale",
}

IMV_CIVITAI_SAMPLER_MAP = {
    'Euler a': 'euler_ancestral',
    'Euler': 'euler',
    'LMS': 'lms',
    'Heun': 'heun',
    'DPM2': 'dpm_2',
    'DPM2 a': 'dpm_2_ancestral',
    'DPM++ 2S a': 'dpmpp_2s_ancestral',
    'DPM++ 2M': 'dpmpp_2m',
    'DPM++ SDE': 'dpmpp_sde',
    'DPM++ 2M SDE': 'dpmpp_2m_sde',
    'DPM++ 3M SDE': 'dpmpp_3m_sde',
    'DPM fast': 'dpm_fast',
    'DPM adaptive': 'dpm_adaptive',
    'DDIM': 'ddim',
    'PLMS': 'plms',
    'UniPC': 'uni_pc',
    'LCM': 'lcm',
}

INV_CIVITAI_SCHEDULER_MAP = {
    'Karras': 'karras',
    'Exponential': 'exponential',
    'SGM Uniform': 'sgm_uniform',
    'Simple': 'simple',
    'DDIM Uniform': 'ddim_uniform',
    'Beta': 'beta',
    'Linear Quadratic': 'linear_quadratic',
    'KL Optimal': 'kl_optimal',
    'Simple Test': 'simple_test',
}


# ============================================================================
# Handler functions for specific generation tools
# ============================================================================

def handle_auto1111(params):
    if params and "\nSteps:" in params:
        if "Negative prompt:" in params:
            prompt_index = [params.index("\nNegative prompt:"), params.index("\nSteps:")]
            neg = params[prompt_index[0] + 1 + len("Negative prompt: "):prompt_index[-1]]
        else:
            prompt_index = [params.index("\nSteps:")]
            neg = ""
        pos = params[:prompt_index[0]]
        return pos, neg
    elif params:
        if "Negative prompt:" in params:
            prompt_index = [params.index("\nNegative prompt:")]
            neg = params[prompt_index[0] + 1 + len("Negative prompt: "):]
        else:
            prompt_index = [len(params)]
            neg = ""
        pos = params[:prompt_index[0]]
        return pos, neg
    else:
        return "", ""


def handle_ezdiff(params):
    data = json.loads(params)
    if data.get("prompt"):
        ed = EASYDIFFUSION_MAPPING_B
    else:
        ed = EASYDIFFUSION_MAPPING_A
    pos = data.get(ed["prompt"])
    data.pop(ed["prompt"])
    neg = data.get(ed["negative_prompt"])
    return pos, neg


def handle_invoke_modern(params):
    meta = json.loads(params.get("sd-metadata"))
    img = meta.get("image")
    prompt = img.get("prompt")
    index = [prompt.rfind("["), prompt.rfind("]")]
    if -1 not in index:
        pos = prompt[:index[0]]
        neg = prompt[index[0] + 1:index[1]]
        return pos, neg
    else:
        return prompt, ""


def handle_invoke_legacy(params):
    dream = params.get("Dream")
    pi = dream.rfind('"')
    ni = [dream.rfind("["), dream.rfind("]")]
    if -1 not in ni:
        pos = dream[1:ni[0]]
        neg = dream[ni[0] + 1:ni[1]]
        return pos, neg
    else:
        pos = dream[1:pi]
        return pos, ""


def handle_novelai(params):
    pos = params.get("Description")
    comment = params.get("Comment", "{}")
    comment_json = json.loads(comment)  # type: ignore
    neg = comment_json.get("uc")
    return pos, neg


def handle_drawthings(params):
    try:
        data = minidom.parseString(params.get("XML:com.adobe.xmp"))
        data_json = json.loads(data.getElementsByTagName("exif:UserComment")[0].childNodes[1].childNodes[1].childNodes[0].data)  # type: ignore
    except Exception:
        return "", ""
    else:
        pos = data_json.get("c")
        neg = data_json.get("uc")
        return pos, neg


# ============================================================================
# ComfyUI metadata handler (with workflow-based prompt tracing)
# ============================================================================

def handle_comfyui(params):
    # Extract generation data from ComfyUI embedded metadata.
    # Supports both 'parameters' string (Auto1111 format) and
    # 'workflow' JSON (ComfyUI native format with KSampler tracing).
    gen_data = {}

    if "parameters" in params:
        gen_data["parameters"] = params["parameters"]

    if "workflow" in params:
        try:
            gen_data["workflow"] = json.loads(params["workflow"])
        except Exception:
            gen_data["workflow"] = params["workflow"]

    if "lora_weights" in params:
        try:
            gen_data["lora_weights"] = json.loads(params["lora_weights"])
        except Exception:
            gen_data["lora_weights"] = params["lora_weights"]

    # Try to extract prompts from workflow if parameters field is missing
    if "parameters" not in gen_data and "workflow" in gen_data and isinstance(gen_data["workflow"], dict):
        try:
            _extract_prompts_from_workflow(gen_data)
        except Exception as e:
            log.debug(_LOG_PREFIX, f"Failed to parse workflow for prompts: {e}")

    if "parameters" in gen_data:
        _parse_parameters_string(gen_data)

    # Apply Civitai mappings to convert human names to keys
    if "sampler_name" in gen_data:
        gen_data["sampler_name"] = IMV_CIVITAI_SAMPLER_MAP.get(gen_data["sampler_name"], gen_data["sampler_name"])
    if "scheduler" in gen_data:
        gen_data["scheduler"] = INV_CIVITAI_SCHEDULER_MAP.get(gen_data["scheduler"], gen_data["scheduler"])

    return gen_data


def _parse_parameters_string(gen_data):
    # Parse the Auto1111-style 'parameters' string for structured generation data.
    params_str = gen_data["parameters"]
    if "Steps:" not in params_str:
        return

    try:
        if "Steps: " in params_str:
            steps_start = params_str.find("Steps: ") + 7
            steps_end = params_str.find(",", steps_start)
            if steps_end == -1:
                steps_end = params_str.find("\n", steps_start)
            gen_data["steps"] = int(params_str[steps_start:steps_end].strip())

        if "Sampler: " in params_str:
            sampler_start = params_str.find("Sampler: ") + 9
            sampler_end = params_str.find(",", sampler_start)
            if sampler_end == -1:
                sampler_end = params_str.find("\n", sampler_start)
            gen_data["sampler_name"] = params_str[sampler_start:sampler_end].strip()

        # Separate scheduler from sampler if embedded (e.g. "DPM++ 2M Karras")
        if gen_data.get("sampler_name"):
            sampler_full = gen_data["sampler_name"]
            schedulers_to_check = set(INV_CIVITAI_SCHEDULER_MAP.keys()) | set(SCHEDULERS_ANY)
            if isinstance(sampler_full, str):
                sampler_full_l = sampler_full.lower()
                for sched in schedulers_to_check:
                    try:
                        sched_l = str(sched).lower()
                        if re.search(r"(?:\s|^)" + re.escape(sched_l) + r"\s*$", sampler_full_l):
                            sched_start = sampler_full_l.rfind(sched_l)
                            if sched_start >= 0:
                                actual_sched = sampler_full[sched_start:]
                                gen_data["sampler_name"] = sampler_full[:sched_start].strip()
                                gen_data["scheduler"] = actual_sched
                                break
                    except Exception:
                        continue

        if "CFG scale: " in params_str:
            cfg_start = params_str.find("CFG scale: ") + 11
            cfg_end = params_str.find(",", cfg_start)
            if cfg_end == -1:
                cfg_end = params_str.find("\n", cfg_start)
            gen_data["cfg_scale"] = float(params_str[cfg_start:cfg_end].strip())

        if "Seed: " in params_str:
            seed_start = params_str.find("Seed: ") + 6
            seed_end = params_str.find(",", seed_start)
            if seed_end == -1:
                seed_end = params_str.find("\n", seed_start)
            gen_data["seed"] = int(params_str[seed_start:seed_end].strip())

        if "Size: " in params_str:
            size_start = params_str.find("Size: ") + 6
            size_end = params_str.find(",", size_start)
            if size_end == -1:
                size_end = params_str.find("\n", size_start)
            size_str = params_str[size_start:size_end].strip()
            if "x" in size_str:
                width, height = size_str.split("x")
                gen_data["width_param"] = int(width.strip())
                gen_data["height_param"] = int(height.strip())

        if "Hashes: " in params_str:
            hashes_start = params_str.find("Hashes: ") + 8
            hashes_end = params_str.find("}", hashes_start) + 1
            if hashes_end > hashes_start:
                hashes_str = params_str[hashes_start:hashes_end]
                try:
                    gen_data["model_hashes"] = json.loads(hashes_str)
                except Exception:
                    gen_data["model_hashes"] = hashes_str

        if "Version: " in params_str:
            version_start = params_str.find("Version: ") + 9
            version_end = params_str.find("\n", version_start)
            if version_end == -1:
                version_end = len(params_str)
            gen_data["version"] = params_str[version_start:version_end].strip()

    except Exception:
        pass


def _extract_prompts_from_workflow(gen_data):
    # Extract prompts by tracing KSampler connections in ComfyUI workflow JSON.
    workflow = gen_data["workflow"]
    nodes = workflow.get("nodes", [])
    links = workflow.get("links", [])

    # Build node ID to node mapping for connection tracing
    node_map = {}
    for node in nodes:
        if isinstance(node, dict) and "id" in node:
            node_map[node["id"]] = node

    def is_valid_prompt_text(text):
        if not text or not isinstance(text, str):
            return False
        text = text.strip()
        if len(text) == 0 or text.isdigit() or len(text) < 3:
            return False
        if any(pattern in text for pattern in ['a + ", " + b', 'CLIP_G', 'CLIP_L']):
            return False
        return True

    def find_connected_node_by_link(link_id):
        for link_data in links:
            if isinstance(link_data, list) and len(link_data) >= 3:
                if link_data[0] == link_id:
                    source_node_id = link_data[1]
                    return node_map.get(source_node_id)
        return None

    def get_text_from_node(node, visited=None):
        if visited is None:
            visited = set()

        if not isinstance(node, dict):
            return None

        node_id = node.get("id")
        if node_id in visited:
            return None
        visited.add(node_id)

        node_type = node.get("type", "")
        widgets_values = node.get("widgets_values", [])

        # For Text Multiline nodes, check widget value first
        if node_type in ["Text Multiline", "String Multiline [RvTools]", "Text", "String"]:
            if widgets_values and len(widgets_values) > 0:
                text = str(widgets_values[0]).strip()
                if is_valid_prompt_text(text):
                    return text

        # Follow STRING/text type input connections
        inputs = node.get("inputs", [])
        for input_def in inputs:
            if not isinstance(input_def, dict):
                continue

            input_type = input_def.get("type", "")
            input_name = input_def.get("name", "")

            if input_type not in ["STRING", "*", "CONDITIONING"] and input_name not in ["text", "text_g", "text_l", "string_1", "string_2", "conditioning"]:
                continue

            link = input_def.get("link")
            if link is not None:
                source_node = find_connected_node_by_link(link)
                if source_node:
                    source_text = get_text_from_node(source_node, visited)
                    if source_text:
                        return source_text

        # Fallback: check widgets for any valid text
        if widgets_values and len(widgets_values) > 0:
            text = str(widgets_values[0]).strip()
            if is_valid_prompt_text(text):
                return text

        return None

    # Strategy 1: Trace backwards from KSampler nodes
    sampler_prompts_pos = []
    sampler_prompts_neg = []

    for node in nodes:
        if not isinstance(node, dict):
            continue

        node_type = node.get("type", "")
        if node_type not in ["KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"]:
            continue

        # Extract sampler settings from the first KSampler found
        if "seed" not in gen_data:
            widgets_values = node.get("widgets_values", [])
            if widgets_values and len(widgets_values) >= 6:
                try:
                    gen_data["seed"] = int(widgets_values[0])
                    gen_data["steps"] = int(widgets_values[1])
                    gen_data["cfg_scale"] = float(widgets_values[2])
                    gen_data["sampler_name"] = str(widgets_values[3])
                    gen_data["scheduler"] = str(widgets_values[4])
                except (ValueError, IndexError):
                    pass

        # Find positive and negative conditioning inputs
        inputs = node.get("inputs", [])
        for input_def in inputs:
            if not isinstance(input_def, dict):
                continue

            input_name = input_def.get("name", "")
            link = input_def.get("link")

            if link is None:
                continue

            if input_name == "positive":
                conditioning_node = find_connected_node_by_link(link)
                if conditioning_node:
                    text = get_text_from_node(conditioning_node)
                    if text:
                        sampler_prompts_pos.append(text)

            elif input_name == "negative":
                conditioning_node = find_connected_node_by_link(link)
                if conditioning_node:
                    text = get_text_from_node(conditioning_node)
                    if text:
                        sampler_prompts_neg.append(text)

    if sampler_prompts_pos:
        gen_data["text_pos_from_workflow"] = sampler_prompts_pos[-1]

    if sampler_prompts_neg:
        gen_data["text_neg_from_workflow"] = sampler_prompts_neg[-1]

    # Strategy 2: Fallback to priority-based search
    if "text_pos_from_workflow" not in gen_data or "text_neg_from_workflow" not in gen_data:
        positive_candidates = []
        negative_candidates = []

        for node in nodes:
            if not isinstance(node, dict):
                continue

            node_type = node.get("type", "")
            node_id = node.get("id", 0)

            # Skip bypassed nodes (mode 2 = muted/bypassed in ComfyUI)
            node_mode = node.get("mode", 0)
            if node_mode == 2:
                continue

            if node_type in ["CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeFlux",
                             "ShowText|pysssss", "Text Multiline", "String Multiline [RvTools]",
                             "String Multiline [Eclipse]"]:
                title = node.get("title", "").lower()
                widgets_values = node.get("widgets_values", [])

                text = None
                if "showtext" in node_type.lower() or "string multiline" in node_type.lower() or "text multiline" in node_type.lower():
                    if widgets_values and len(widgets_values) > 0:
                        text = str(widgets_values[0]).strip()
                else:
                    text = get_text_from_node(node)

                if not text or not is_valid_prompt_text(text):
                    continue

                text_length = len(text)

                priority = 0
                if "primary prompt" in title or "final prompt" in title:
                    priority = 100
                elif "prompt preview" in title or "\U0001f40d prompt" in title:
                    priority = 90
                elif "primary" in title:
                    priority = 80
                elif "pos" in title or "prompt" in title:
                    priority = 50
                elif "neg" in title or "negative" in title:
                    priority = 50

                if "neg" in title or "negative" in title or "cte-" in title:
                    negative_candidates.append((priority, text_length, node_id, text))
                else:
                    positive_candidates.append((priority, text_length, node_id, text))

        if "text_pos_from_workflow" not in gen_data and positive_candidates:
            positive_candidates.sort(reverse=False, key=lambda x: (-x[0], -x[1], x[2]))
            gen_data["text_pos_from_workflow"] = positive_candidates[0][3]

        if "text_neg_from_workflow" not in gen_data and negative_candidates:
            negative_candidates.sort(reverse=False, key=lambda x: (-x[0], -x[1], x[2]))
            gen_data["text_neg_from_workflow"] = negative_candidates[0][3]


# ============================================================================
# Main extraction function
# ============================================================================

def extract_image_metadata(img) -> Dict[str, Any]:
    # Extract metadata from a PIL Image object and return a pipe dict.
    # Detects: ComfyUI, Auto1111, EasyDiffusion, InvokeAI, NovelAI, DrawThings.
    #
    # Returns dict with keys: steps, sampler_name, scheduler, cfg, seed,
    #   width, height, text_pos, text_neg, model_name, path
    prompt = ""
    negative = ""
    width = img.width
    height = img.height
    steps = 0
    sampler = ""
    scheduler = ""
    cfg_scale = 0.0
    seed = 0
    model_hashes = {}
    comfyui_processed = False

    if img.format == "PNG" or ("parameters" in img.info or "workflow" in img.info or "lora_weights" in img.info):
        if "parameters" in img.info or "workflow" in img.info or "lora_weights" in img.info:
            gen_data = handle_comfyui(img.info)
            if gen_data:
                comfyui_processed = True
                steps = gen_data.get("steps", 0)
                sampler = gen_data.get("sampler_name", "")
                scheduler = gen_data.get("scheduler", "")
                cfg_scale = gen_data.get("cfg_scale", 0.0)
                seed = gen_data.get("seed", 0)
                model_hashes = gen_data.get("model_hashes", {})

                if "parameters" in gen_data:
                    params_str = gen_data["parameters"]
                    if "Negative prompt:" in params_str:
                        parts = params_str.split("Negative prompt:")
                        if len(parts) >= 2:
                            prompt = parts[0].strip()
                            neg_part = parts[1]
                            if "Steps:" in neg_part:
                                negative = neg_part.split("Steps:")[0].strip()
                            else:
                                negative = neg_part.strip()
                    else:
                        if "Steps:" in params_str:
                            prompt = params_str.split("Steps:")[0].strip()
                        else:
                            prompt = params_str.strip()

                # Use workflow-extracted prompts if parameters field didn't provide them
                if not prompt and "text_pos_from_workflow" in gen_data:
                    prompt = gen_data["text_pos_from_workflow"]
                if not negative and "text_neg_from_workflow" in gen_data:
                    negative = gen_data["text_neg_from_workflow"]

        elif "parameters" in img.info and not comfyui_processed:
            params = img.info.get("parameters")
            prompt, negative = handle_auto1111(params)

        elif "negative_prompt" in img.info or "Negative Prompt" in img.info:
            params = str(img.info).replace("'", '"')
            prompt, negative = handle_ezdiff(params)

        elif "sd-metadata" in img.info:
            prompt, negative = handle_invoke_modern(img.info)

        elif "Dream" in img.info:
            prompt, negative = handle_invoke_legacy(img.info)

        elif img.info.get("Software") == "NovelAI":
            prompt, negative = handle_novelai(img.info)

        elif "XML:com.adobe.xmp" in img.info:
            prompt, negative = handle_drawthings(img.info)

    # Extract model name from hashes if available
    model_name = ""
    if isinstance(model_hashes, dict):
        for key in model_hashes.keys():
            if key.startswith("Model:"):
                model_name = key.replace("Model:", "", 1)
                break

    # Only include keys with actual values — omit empty/zero defaults
    # so downstream nodes can distinguish "no metadata" from real data.
    candidates = {
        "steps": steps,
        "sampler_name": sampler,
        "scheduler": scheduler,
        "cfg": cfg_scale,
        "seed": seed,
        "width": width,
        "height": height,
        "text_pos": prompt,
        "text_neg": negative,
        "model_name": model_name,
    }
    return {k: v for k, v in candidates.items() if v and v != 0 and v != 0.0}
