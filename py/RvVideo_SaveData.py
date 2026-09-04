# Save Video with Generation Data [Eclipse]
#
# Standalone IMAGE-batch to MP4 output node. The implementation intentionally
# owns its video, metadata, filename, and sidecar logic instead of importing
# either Eclipse save-node module.

import glob
import json
import math
import os
import re
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import av  # type: ignore
import folder_paths  # type: ignore
import numpy as np  # type: ignore
import torch  # type: ignore
from comfy.cli_args import args  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.common import make_comfy_progress
from ..core.image_helpers import (
    cat_and_fit_images,
    flatten_images,
    prepare_image_output,
    single_input_batch,
    unwrap_value,
    was_input_batch,
)
from ..core.logger import log
from ..core.model_integrity import read_expected, sha256_for

_LOG_PREFIX = "SaveVideoData"
_LOOP_DOWNSAMPLE_SIZE = 512
_LOOP_MATCH_CHUNK_SIZE = 16
_LOOP_METRICS = ["ncc", "mse", "luminance_mse", "gradient_mse"]
_H264_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]
_MODEL_EXTENSIONS = [".safetensors", ".pt", ".pth", ".ckpt", ".bin", ".gguf"]
_LORA_TAG = re.compile(r"<lora:([^>:]+)(?::([^>]+))?>", re.IGNORECASE)
_EMBEDDING_TAG = re.compile(r"embedding:([^,\s()\:]+)", re.IGNORECASE)
_MISSING_VALUES = (None, "", "undefined", "none", "None")

_CIVITAI_SAMPLERS = {
    "euler_ancestral": "Euler a",
    "euler": "Euler",
    "lms": "LMS",
    "heun": "Heun",
    "dpm_2": "DPM2",
    "dpm_2_ancestral": "DPM2 a",
    "dpmpp_2s_ancestral": "DPM++ 2S a",
    "dpmpp_2m": "DPM++ 2M",
    "dpmpp_sde": "DPM++ SDE",
    "dpmpp_2m_sde": "DPM++ 2M SDE",
    "dpmpp_3m_sde": "DPM++ 3M SDE",
    "dpm_fast": "DPM fast",
    "dpm_adaptive": "DPM adaptive",
    "ddim": "DDIM",
    "plms": "PLMS",
    "uni_pc_bh2": "UniPC",
    "uni_pc": "UniPC",
    "lcm": "LCM",
}

_SCHEDULER_SUFFIXES = {
    "karras": " Karras",
    "exponential": " Exponential",
    "sgm_uniform": " SGM Uniform",
    "simple": " Simple",
    "ddim_uniform": " DDIM Uniform",
    "beta": " Beta",
    "linear_quadratic": " Linear Quadratic",
    "kl_optimal": " kl optimal",
    "AYS SDXL": " AYS SDXL",
    "AYS SD1": " AYS SD1",
    "AYS SVD": " AYS SVD",
    "simple_test": " Simple Test",
}


class FilenameProcessor:
    """Image Save-compatible placeholder expansion and path sanitization."""

    def __init__(self, values: dict[str, Any] | None = None):
        self.values = values or {}
        self.placeholders = {
            "%today": self._date,
            "%date": self._date,
            "%time": self._time,
            "%Y": lambda: self._now().strftime("%Y"),
            "%y": lambda: self._now().strftime("%y"),
            "%m": lambda: self._now().strftime("%m"),
            "%M": lambda: self._now().strftime("%m"),
            "%d": lambda: self._now().strftime("%d"),
            "%D": lambda: self._now().strftime("%d"),
            "%H": lambda: self._now().strftime("%H"),
            "%S": lambda: self._now().strftime("%S"),
            "%basemodel": lambda: self.values.get("basemodel", ""),
            "%model": lambda: self.values.get("model", ""),
            "%seed": lambda: self.values.get("seed", ""),
            "%sampler_name": lambda: self.values.get("sampler_name", ""),
            "%scheduler": lambda: self.values.get("scheduler", ""),
            "%steps": lambda: self.values.get("steps", ""),
            "%cfg": lambda: self.values.get("cfg", ""),
            "%denoise": lambda: self.values.get("denoise", ""),
            "%clip_skip": lambda: self.values.get("clip_skip", ""),
        }

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).astimezone()

    @classmethod
    def _date(cls) -> str:
        return cls._now().strftime("%Y-%m-%d")

    @classmethod
    def _time(cls) -> str:
        return cls._now().strftime("%H%M%S")

    def process(self, value: str) -> str:
        result = value if isinstance(value, str) and value else "default"
        used = [key for key in self.placeholders if key in result]
        for placeholder in sorted(used, key=len, reverse=True):
            resolved = self.placeholders[placeholder]()
            if resolved in _MISSING_VALUES:
                resolved = placeholder.lstrip("%")
            result = result.replace(placeholder, str(resolved))
        return self.sanitize_path(result)

    @staticmethod
    def sanitize_filename(value: str) -> str:
        invalid = '<>:"/\\|?*' + "".join(chr(i) for i in range(32))
        for char in invalid:
            value = value.replace(char, "_")
        value = value.strip(" .")
        if not value:
            return "untitled"
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update({f"COM{i}" for i in range(1, 10)})
        reserved.update({f"LPT{i}" for i in range(1, 10)})
        if value.split(".")[0].upper() in reserved:
            value = "_" + value
        if len(value) > 255:
            base, extension = os.path.splitext(value)
            value = base[: 255 - len(extension)] + extension
        return value

    @classmethod
    def sanitize_path(cls, value: str) -> str:
        value = value.replace("\\", "/")
        parts = Path(value).parts
        sanitized = []
        for index, part in enumerate(parts):
            if index == 0 and part in (os.sep, "/"):
                sanitized.append(part)
                continue
            if index == 0 and len(parts) > 1 and part.endswith(":"):
                sanitized.append(part)
                continue
            for char in '<>:"|?*' + "".join(chr(i) for i in range(32)):
                part = part.replace(char, "_")
            part = part.strip(" .") or "unnamed"
            sanitized.append(part)
        return str(Path(*sanitized)) if sanitized else "default"


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value in _MISSING_VALUES)


def _string_value(value: Any) -> str:
    if _is_missing(value):
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, torch.Tensor):
        return str(value.item()) if value.numel() == 1 else str(value.tolist())
    if isinstance(value, np.generic):
        return str(value.item())
    if isinstance(value, (list, tuple)):
        return " ".join(filter(None, (_string_value(item) for item in value)))
    return str(value)


def _basename_without_extension(value: str) -> str:
    return os.path.splitext(os.path.basename(value))[0]


def _unwrap_pipe(pipe: Any) -> dict[str, Any] | None:
    pipe = unwrap_value(pipe, None)
    if pipe is None:
        return None
    if isinstance(pipe, tuple):
        pipe = pipe[0] if pipe else None
    if not isinstance(pipe, dict):
        raise TypeError(
            "Save Video with Generation Data expects a dict-style or tuple-style PIPE."
        )
    return pipe


def _placeholder_values(context: dict[str, Any] | None) -> dict[str, str]:
    values = {
        "model": "",
        "basemodel": "",
        "seed": "",
        "sampler_name": "",
        "scheduler": "",
        "steps": "",
        "cfg": "",
        "denoise": "",
        "clip_skip": "",
    }
    if context is None:
        return values
    model_names = _deduplicate_names(context.get("model_name"))
    if model_names:
        values["model"] = model_names[0]
        values["basemodel"] = _basename_without_extension(model_names[0])
    for key in ("seed", "sampler_name", "scheduler", "steps", "cfg", "clip_skip"):
        values[key] = _string_value(context.get(key))
    denoise = context.get("denoise")
    if denoise is None:
        denoise = context.get("guidance")
    values["denoise"] = _string_value(denoise)
    return values


def _resolve_filename_prefix(
    output_directory: str, filename_prefix: str, values: dict[str, str]
) -> tuple[str, str, str]:
    output_root = os.path.abspath(output_directory)
    resolved = FilenameProcessor(values).process(filename_prefix)
    relative_directory, filename_stem = os.path.split(resolved)
    save_directory = os.path.abspath(os.path.join(output_root, relative_directory))
    try:
        inside_output = os.path.commonpath((output_root, save_directory)) == output_root
    except ValueError:
        inside_output = False
    if not inside_output:
        raise ValueError("filename_prefix must resolve inside the ComfyUI output directory.")
    os.makedirs(save_directory, exist_ok=True)
    filename_stem = FilenameProcessor.sanitize_filename(filename_stem)
    subfolder = os.path.relpath(save_directory, output_root)
    if subfolder == ".":
        subfolder = ""
    return save_directory, filename_stem, subfolder


def _next_counter(save_directory: str, filename_stem: str) -> int:
    pattern = re.compile(rf"^{re.escape(filename_stem)}_(\d{{4,}})_?\.mp4$")
    counters = []
    for filename in os.listdir(save_directory):
        match = pattern.match(filename)
        if match:
            counters.append(int(match.group(1)))
    return max(counters, default=0) + 1


def _video_filename(filename_stem: str, counter: int) -> str:
    return f"{filename_stem}_{counter:04}.mp4"


def _deduplicate_names(value: Any) -> list[str]:
    text = _string_value(value)
    if not text:
        return []
    seen = set()
    result = []
    for item in text.split(","):
        item = item.strip()
        key = _basename_without_extension(item).lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _sha256(path: str) -> str | None:
    expected = read_expected(path)
    if expected and expected.get("sha256"):
        return str(expected["sha256"])
    return sha256_for(
        path,
        use_sidecar=True,
        write_sidecar=True,
        show_progress=True,
    )


def _find_named_file(
    folder_type: str, name: str, extensions: list[str]
) -> str | None:
    direct = folder_paths.get_full_path(folder_type, name)
    if direct and os.path.isfile(direct):
        return direct
    stem = name
    for extension in extensions:
        candidate = stem if stem.lower().endswith(extension) else stem + extension
        direct = folder_paths.get_full_path(folder_type, candidate)
        if direct and os.path.isfile(direct):
            return direct
    try:
        available = folder_paths.get_filename_list(folder_type)
    except Exception:  # noqa: BLE001 - folder providers may raise plugin-specific errors
        available = []
    wanted = _basename_without_extension(name).lower()
    for candidate in available:
        if _basename_without_extension(candidate).lower().startswith(wanted):
            direct = folder_paths.get_full_path(folder_type, candidate)
            if direct and os.path.isfile(direct):
                return direct
    return None


def _find_model_file(name: str) -> tuple[str | None, str | None]:
    folder_types = ("checkpoints", "diffusion_models", "unet", "upscale_models")
    for folder_type in folder_types:
        path = _find_named_file(folder_type, name, _MODEL_EXTENSIONS)
        if path:
            return path, folder_type
    if os.path.isfile(name):
        return name, None
    for folder_type in folder_types:
        try:
            search_directories = folder_paths.get_folder_paths(folder_type)
        except Exception:  # noqa: BLE001 - folder providers may raise plugin-specific errors
            search_directories = []
        for search_directory in search_directories:
            for extension in _MODEL_EXTENSIONS:
                candidate = name if name.lower().endswith(extension) else name + extension
                matches = glob.glob(os.path.join(search_directory, "**", candidate), recursive=True)
                if matches:
                    return matches[0], folder_type
    return None, None


def _parse_loras(value: Any) -> tuple[str, dict[str, float]]:
    text = _string_value(value)
    if not text:
        return "", {}
    matches = _LORA_TAG.findall(text)
    parts = matches if matches else [(part, "") for part in re.split(r"[,;\s]+", text) if part]
    tokens = []
    weights = {}
    for raw_name, raw_weight in parts:
        name = Path(raw_name.split(":", 1)[0]).stem
        if not name or name.lower() in {item.lower() for item in weights}:
            continue
        if not raw_weight and ":" in raw_name:
            raw_name, raw_weight = raw_name.split(":", 1)
            name = Path(raw_name).stem
        try:
            weight = float(raw_weight) if raw_weight else 1.0
        except (TypeError, ValueError):
            weight = 1.0
        weights[name] = weight
        tokens.append(f"<lora:{name}:{weight}>")
    return "".join(tokens), weights


def _resource_hashes(
    context: dict[str, Any], positive: str, negative: str, lora_tokens: str
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for model_name in _deduplicate_names(context.get("model_name")):
        path, folder_type = _find_model_file(model_name)
        if not path:
            log.warning(_LOG_PREFIX, f"Model file not found for hash: {model_name}")
            continue
        digest = _sha256(path)
        if digest:
            name = _basename_without_extension(model_name)
            key = name if folder_type == "upscale_models" else f"Model:{name}"
            hashes[key] = digest[:10]

    for vae_name in _deduplicate_names(context.get("vae_name")):
        path = _find_named_file("vae", vae_name, _MODEL_EXTENSIONS)
        if path:
            digest = _sha256(path)
            if digest:
                hashes[_basename_without_extension(vae_name)] = digest[:10]
        else:
            log.warning(_LOG_PREFIX, f"VAE file not found for hash: {vae_name}")

    prompt_text = f"{positive} {negative} {lora_tokens}"
    seen_loras = set()
    for raw_name, _weight in _LORA_TAG.findall(prompt_text):
        name = Path(raw_name).stem
        if name.lower() in seen_loras:
            continue
        seen_loras.add(name.lower())
        path = _find_named_file("loras", name, [".safetensors", ".pt", ".bin"])
        if path:
            digest = _sha256(path)
            if digest:
                hashes[f"LORA:{_basename_without_extension(path)}"] = digest[:10]
        else:
            log.warning(_LOG_PREFIX, f"LoRA file not found for hash: {name}")

    seen_embeddings = set()
    for name in _EMBEDDING_TAG.findall(prompt_text):
        if name.lower() in seen_embeddings:
            continue
        seen_embeddings.add(name.lower())
        path = _find_named_file("embeddings", name, [".safetensors", ".pt", ".bin"])
        if path:
            digest = _sha256(path)
            if digest:
                hashes[f"embed:{name}"] = digest[:10]
        else:
            log.warning(_LOG_PREFIX, f"Embedding file not found for hash: {name}")
    return hashes


def _civitai_sampler(sampler: str, scheduler: str) -> str:
    sampler = sampler.replace("_gpu", "")
    mapped = _CIVITAI_SAMPLERS.get(sampler, sampler or "Euler")
    if sampler in _CIVITAI_SAMPLERS:
        return mapped + _SCHEDULER_SUFFIXES.get(scheduler, "")
    if scheduler and scheduler != "normal":
        return f"{mapped}_{scheduler}"
    return mapped


def _generation_parameters(
    context: dict[str, Any],
    width: int,
    height: int,
    remove_prompts: bool,
    add_loras_to_prompt: bool,
) -> str:
    positive = _string_value(context.get("text_pos"))
    negative = _string_value(context.get("text_neg"))
    lora_tokens, _weights = _parse_loras(
        context.get("lora_names") or context.get("loras")
    )
    hashes = _resource_hashes(context, positive, negative, lora_tokens)
    if remove_prompts:
        positive_for_metadata = ""
        negative_for_metadata = ""
    else:
        positive_for_metadata = positive + (lora_tokens if add_loras_to_prompt else "")
        negative_for_metadata = negative

    sampler = _string_value(context.get("sampler_name"))
    scheduler = _string_value(context.get("scheduler"))
    details = [
        f"Steps: {_string_value(context.get('steps'))}",
        f"Sampler: {_civitai_sampler(sampler, scheduler)}",
        f"Schedule type: {scheduler}",
        f"CFG scale: {_string_value(context.get('cfg'))}",
        f"Seed: {_string_value(context.get('seed'))}",
        f"Size: {width}x{height}",
    ]
    denoise = context.get("denoise")
    if denoise is None:
        denoise = context.get("guidance")
    if _string_value(denoise):
        details.append(f"Denoising strength: {_string_value(denoise)}")
    clip_skip = _string_value(context.get("clip_skip"))
    if clip_skip:
        try:
            clip_skip = str(abs(int(float(clip_skip))))
        except (TypeError, ValueError):
            pass
        details.append(f"Clip skip: {clip_skip}")
    models = _deduplicate_names(context.get("model_name"))
    if models:
        model_name = _basename_without_extension(models[0])
        details.append(f"Model: {model_name}")
        model_hash = hashes.get(f"Model:{model_name}")
        if model_hash:
            details.append(f"Model hash: {model_hash}")
    vaes = _deduplicate_names(context.get("vae_name"))
    if vaes:
        vae_name = _basename_without_extension(vaes[0])
        details.append(f"VAE: {vae_name}")
        vae_hash = hashes.get(vae_name)
        if vae_hash:
            details.append(f"VAE hash: {vae_hash}")
    details.append(f"Hashes: {json.dumps(hashes, separators=(',', ':'))}")
    details.append("Version: ComfyUI")
    positive_for_metadata = " ".join(positive_for_metadata.splitlines()).strip()
    negative_for_metadata = " ".join(negative_for_metadata.splitlines()).strip()
    return (
        f"{positive_for_metadata}\nNegative prompt: {negative_for_metadata}\n"
        + ", ".join(details)
    )


def _metadata(
    prompt: Any,
    extra_pnginfo: Any,
    context: dict[str, Any] | None,
    embed_workflow: bool,
    save_generation_data: bool,
    remove_prompts: bool,
    add_loras_to_prompt: bool,
    width: int,
    height: int,
) -> dict[str, Any] | None:
    if args.disable_metadata:
        return None
    metadata: dict[str, Any] = {}
    if embed_workflow:
        extra = unwrap_value(extra_pnginfo, None)
        if isinstance(extra, dict):
            metadata.update(extra)
        prompt_value = unwrap_value(prompt, None)
        if prompt_value is not None:
            metadata["prompt"] = prompt_value
    if save_generation_data and context is not None:
        metadata["parameters"] = _generation_parameters(
            context,
            width,
            height,
            remove_prompts,
            add_loras_to_prompt,
        )
    return metadata or None


def _save_workflow_json(extra_pnginfo: Any, path_without_extension: str) -> None:
    extra = unwrap_value(extra_pnginfo, None)
    workflow = extra.get("workflow") if isinstance(extra, dict) else None
    if workflow is None:
        log.warning(_LOG_PREFIX, "No workflow metadata found; skipping JSON sidecar.")
        return
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except json.JSONDecodeError:
            pass
    sidecar = path_without_extension + ".json"
    with open(sidecar, "w", encoding="utf-8") as workflow_file:
        json.dump(workflow, workflow_file, ensure_ascii=False, indent=2)
    log.msg(_LOG_PREFIX, f"Workflow saved to: {sidecar}")


def _audio_samples_for_video(num_frames: int, fps: float, sample_rate: int) -> int:
    return max(1, round(num_frames * sample_rate / fps))


def _fit_frame(frame: torch.Tensor, target_h: int, target_w: int) -> torch.Tensor:
    if frame.shape[1] == target_h and frame.shape[2] == target_w:
        return frame
    chw = frame.permute(0, 3, 1, 2)
    resized = torch.nn.functional.interpolate(
        chw, size=(target_h, target_w), mode="bilinear", align_corners=False
    )
    return resized.permute(0, 2, 3, 1)


def _has_mismatched_frame_size(
    frames: list[torch.Tensor], target_h: int, target_w: int
) -> bool:
    return any(
        frame.shape[1] != target_h or frame.shape[2] != target_w for frame in frames
    )


def _gradient_magnitude(images: torch.Tensor) -> torch.Tensor:
    dx = images[:, :, :, 1:] - images[:, :, :, :-1]
    dy = images[:, :, 1:, :] - images[:, :, :-1, :]
    return (dx[:, :, :-1, :].pow(2) + dy[:, :, :, :-1].pow(2)).sqrt().mean(dim=1)


def _frame_similarity_scores(
    candidates: torch.Tensor, reference: torch.Tensor, metric: str
) -> torch.Tensor:
    if metric == "ncc":
        candidate_flat = candidates.reshape(candidates.shape[0], -1)
        reference_flat = reference.reshape(1, -1)
        candidate_flat -= candidate_flat.mean(dim=1, keepdim=True)
        reference_flat -= reference_flat.mean()
        return (candidate_flat * reference_flat).sum(dim=1) / (
            candidate_flat.norm(dim=1) * reference_flat.norm() + 1e-8
        )
    if metric == "luminance_mse":
        weights = torch.tensor([0.299, 0.587, 0.114], device=candidates.device).view(
            1, 3, 1, 1
        )
        return (
            (candidates * weights).sum(dim=1) - (reference * weights).sum(dim=1)
        ).pow(2).mean(dim=(1, 2))
    if metric == "gradient_mse":
        return (
            _gradient_magnitude(candidates) - _gradient_magnitude(reference)
        ).pow(2).mean(dim=(1, 2))
    return (candidates - reference).pow(2).mean(dim=(1, 2, 3))


def _pairwise_scores(
    head: torch.Tensor, tail: torch.Tensor, metric: str
) -> torch.Tensor:
    if metric == "ncc":
        head_features = head.reshape(head.shape[0], -1)
        tail_features = tail.reshape(tail.shape[0], -1)
        head_features -= head_features.mean(dim=1, keepdim=True)
        tail_features -= tail_features.mean(dim=1, keepdim=True)
        return (head_features @ tail_features.T) / (
            head_features.norm(dim=1, keepdim=True)
            * tail_features.norm(dim=1).unsqueeze(0)
            + 1e-8
        )
    if metric == "luminance_mse":
        weights = torch.tensor([0.299, 0.587, 0.114], device=head.device).view(
            1, 3, 1, 1
        )
        head_features = (head * weights).sum(dim=1).reshape(head.shape[0], -1)
        tail_features = (tail * weights).sum(dim=1).reshape(tail.shape[0], -1)
    elif metric == "gradient_mse":
        head_features = _gradient_magnitude(head).reshape(head.shape[0], -1)
        tail_features = _gradient_magnitude(tail).reshape(tail.shape[0], -1)
    else:
        head_features = head.reshape(head.shape[0], -1)
        tail_features = tail.reshape(tail.shape[0], -1)
    feature_count = head_features.shape[1]
    head_sq = head_features.pow(2).sum(dim=1) / feature_count
    tail_sq = tail_features.pow(2).sum(dim=1) / feature_count
    cross = (head_features @ tail_features.T) / feature_count
    return head_sq.unsqueeze(1) + tail_sq.unsqueeze(0) - 2 * cross


def _find_loop_point(
    images: torch.Tensor, search_pct: int, metric: str = "ncc"
) -> tuple[int, float]:
    frame_count = images.shape[0]
    search_count = max(1, round(frame_count * max(1, min(99, search_pct)) / 100.0))
    search_start = max(1, frame_count - search_count)
    height, width = images.shape[1:3]
    scale = min(1.0, _LOOP_DOWNSAMPLE_SIZE / max(height, width))
    size = (max(1, round(height * scale)), max(1, round(width * scale)))
    with torch.inference_mode():
        reference = images[:1, ..., :3].permute(0, 3, 1, 2).float()
        reference = torch.nn.functional.interpolate(
            reference, size=size, mode="bilinear", align_corners=False
        )
        best_index = search_start
        best_score = float("-inf") if metric == "ncc" else float("inf")
        for start in range(search_start, frame_count, _LOOP_MATCH_CHUNK_SIZE):
            stop = min(frame_count, start + _LOOP_MATCH_CHUNK_SIZE)
            candidates = images[start:stop, ..., :3].permute(0, 3, 1, 2).float()
            candidates = torch.nn.functional.interpolate(
                candidates, size=size, mode="bilinear", align_corners=False
            )
            scores = _frame_similarity_scores(candidates, reference, metric)
            local = int(scores.argmax().item()) if metric == "ncc" else int(scores.argmin().item())
            score = float(scores[local].item())
            if (metric == "ncc" and score > best_score) or (
                metric != "ncc" and score < best_score
            ):
                best_index, best_score = start + local, score
    return best_index, best_score


def _find_loop_pair(
    images: torch.Tensor, search_pct: int, metric: str = "ncc"
) -> tuple[int, int, float]:
    frame_count = images.shape[0]
    search_count = max(1, round(frame_count * max(1, min(99, search_pct)) / 100.0))
    head_end = min(search_count, frame_count // 2)
    tail_start = max(frame_count - search_count, frame_count // 2)
    if head_end < 1 or tail_start >= frame_count or head_end > tail_start:
        return 0, frame_count - 1, 0.0
    height, width = images.shape[1:3]
    scale = min(1.0, _LOOP_DOWNSAMPLE_SIZE / max(height, width))
    size = (max(1, round(height * scale)), max(1, round(width * scale)))
    best_head, best_tail = 0, tail_start
    best_score = float("-inf") if metric == "ncc" else float("inf")
    with torch.inference_mode():
        for head_start in range(0, head_end, _LOOP_MATCH_CHUNK_SIZE):
            head_stop = min(head_end, head_start + _LOOP_MATCH_CHUNK_SIZE)
            head = images[head_start:head_stop, ..., :3].permute(0, 3, 1, 2).float()
            head = torch.nn.functional.interpolate(
                head, size=size, mode="bilinear", align_corners=False
            )
            for tail_offset in range(0, frame_count - tail_start, _LOOP_MATCH_CHUNK_SIZE):
                tail_stop = min(
                    frame_count - tail_start, tail_offset + _LOOP_MATCH_CHUNK_SIZE
                )
                tail = images[
                    tail_start + tail_offset : tail_start + tail_stop, ..., :3
                ].permute(0, 3, 1, 2).float()
                tail = torch.nn.functional.interpolate(
                    tail, size=size, mode="bilinear", align_corners=False
                )
                scores = _pairwise_scores(head, tail, metric)
                flat = int(scores.argmax().item()) if metric == "ncc" else int(scores.argmin().item())
                local_head, local_tail = divmod(flat, tail.shape[0])
                score = float(scores[local_head, local_tail].item())
                if (metric == "ncc" and score > best_score) or (
                    metric != "ncc" and score < best_score
                ):
                    best_head = head_start + local_head
                    best_tail = tail_start + tail_offset + local_tail
                    best_score = score
    return best_head, best_tail, best_score


def _encode(
    images: Any,
    fps: float,
    audio: dict[str, Any] | None,
    output_path: str,
    codec: str,
    crf: int,
    preset: str,
    metadata: dict[str, Any] | None,
    height: int,
    width: int,
) -> None:
    container = av.open(
        output_path, mode="w", options={"movflags": "use_metadata_tags+faststart"}
    )
    try:
        if metadata:
            for key, value in metadata.items():
                container.metadata[key] = (
                    value if isinstance(value, str) else json.dumps(value)
                )
        encoded_width = width - (width % 2)
        encoded_height = height - (height % 2)
        video_stream = container.add_stream(
            codec, rate=Fraction(round(fps * 1000), 1000)
        )
        if not isinstance(video_stream, av.VideoStream):
            raise TypeError("Failed to create video stream")
        video_stream.width = encoded_width
        video_stream.height = encoded_height
        video_stream.pix_fmt = "yuv420p"
        video_stream.options = {"crf": str(crf), "preset": preset}

        audio_stream = None
        if (
            isinstance(audio, dict)
            and "waveform" in audio
            and "sample_rate" in audio
        ):
            try:
                waveform = audio["waveform"]
                if waveform.ndim == 3:
                    waveform = waveform[0]
                audio_stream = container.add_stream("aac", rate=int(audio["sample_rate"]))
                audio_stream.layout = "stereo" if waveform.shape[0] >= 2 else "mono"
            except Exception as error:  # noqa: BLE001 - invalid audio must not lose video
                log.warning(_LOG_PREFIX, f"Audio stream init failed, skipping audio: {error}")
                audio_stream = None

        progress = make_comfy_progress(len(images) + 1)
        for frame in images:
            if frame.dim() == 3:
                frame = frame.unsqueeze(0)
            frame = _fit_frame(frame, height, width)[0]
            array = (
                torch.clamp(frame[..., :3] * 255.0, min=0, max=255)
                .detach()
                .to(device=torch.device("cpu"), dtype=torch.uint8)
                .numpy()
            )
            array = array[:encoded_height, :encoded_width, :]
            video_frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in video_stream.encode(video_frame):
                container.mux(packet)
            progress.update(1)
        for packet in video_stream.encode():
            container.mux(packet)

        if audio_stream is not None and audio is not None:
            try:
                waveform = audio["waveform"]
                if waveform.ndim == 3:
                    waveform = waveform[0]
                audio_array = (
                    waveform.detach()
                    .to(device=torch.device("cpu"), dtype=torch.float32)
                    .contiguous()
                    .numpy()
                )
                audio_frame = av.AudioFrame.from_ndarray(
                    audio_array,
                    format="fltp",
                    layout="stereo" if audio_array.shape[0] >= 2 else "mono",
                )
                audio_frame.sample_rate = int(audio["sample_rate"])
                for packet in audio_stream.encode(audio_frame):
                    container.mux(packet)
                for packet in audio_stream.encode():
                    container.mux(packet)
            except Exception as error:  # noqa: BLE001 - invalid audio must not lose video
                log.warning(
                    _LOG_PREFIX, f"Audio encode failed (video still saved): {error}"
                )
    finally:
        container.close()
    progress.update(1)


class RvVideo_SaveData(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        placeholders = (
            "%today, %date, %time, %Y, %y, %m/%M, %d/%D, %H, %S, "
            "%basemodel, %model, %seed, %sampler_name, %scheduler, %steps, "
            "%cfg, %denoise, %clip_skip"
        )
        return io.Schema(
            node_id="Save Video Data [Eclipse]",
            display_name="Save Video with Generation Data",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description=(
                "Save an IMAGE batch and optional AUDIO as MP4 with optional raw "
                "ComfyUI workflow metadata, A1111-compatible generation data, and "
                "a workflow JSON sidecar. filename_prefix accepts Image Save-style "
                "placeholders and nested relative folders."
            ),
            inputs=[
                io.Image.Input("images", tooltip="Required batch of video frames."),
                io.String.Input(
                    "features",
                    default="embed_workflow,save_gen_data,trim",
                    socketless=True,
                    tooltip="Feature state used by the combo-chip interface.",
                ),
                io.Boolean.Input(
                    "embed_workflow",
                    default=True,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Embed raw ComfyUI prompt and workflow metadata.",
                ),
                io.Boolean.Input(
                    "save_generation_data",
                    default=True,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Embed A1111-compatible parameters when PIPE is connected.",
                ),
                io.Boolean.Input(
                    "remove_prompts",
                    default=False,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Blank both prompts in generation metadata.",
                ),
                io.Boolean.Input(
                    "save_workflow_as_json",
                    default=False,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Write the workflow beside the MP4 as JSON.",
                ),
                io.Boolean.Input(
                    "add_loras_to_prompt",
                    default=False,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Append used LoRA names and weights to positive prompt metadata.",
                ),
                io.Boolean.Input(
                    "enable_trim",
                    default=True,
                    socketless=True,
                    label_on="yes",
                    label_off="no",
                    tooltip="Enable trim and loop controls.",
                ),
                io.Float.Input(
                    "fps",
                    default=24.0,
                    min=1.0,
                    max=240.0,
                    step=0.01,
                    tooltip="Output video frame rate.",
                ),
                io.String.Input(
                    "filename_prefix",
                    default="video/ComfyUI_Eclipse",
                    tooltip=f"Relative output prefix. Supported placeholders: {placeholders}.",
                ),
                io.Combo.Input(
                    "format",
                    options=["mp4"],
                    default="mp4",
                    tooltip="Output container format.",
                ),
                io.Combo.Input(
                    "codec",
                    options=["h264"],
                    default="h264",
                    tooltip="Output video codec.",
                ),
                io.Int.Input(
                    "crf",
                    default=19,
                    min=0,
                    max=51,
                    step=1,
                    tooltip="Quality factor; lower values give higher quality.",
                ),
                io.Combo.Input(
                    "preset",
                    options=_H264_PRESETS,
                    default="veryfast",
                    tooltip="H.264 compression-speed preset.",
                ),
                io.Combo.Input(
                    "trim_mode",
                    options=[
                        "none",
                        "video_to_audio",
                        "audio_to_video",
                        "shortest",
                        "loop_match",
                        "loop_match_blend",
                    ],
                    default="video_to_audio",
                    tooltip="Align video/audio or find a seamless loop point.",
                ),
                io.Int.Input(
                    "loop_search_pct",
                    default=50,
                    min=1,
                    max=99,
                    step=1,
                    tooltip="Percentage of head/tail frames searched for a loop match.",
                ),
                io.Int.Input(
                    "loop_blend_frames",
                    default=8,
                    min=0,
                    max=60,
                    step=1,
                    tooltip="Tail frames blended toward the loop start.",
                ),
                io.Combo.Input(
                    "loop_metric",
                    options=_LOOP_METRICS,
                    default="ncc",
                    tooltip="Similarity metric used for loop-point detection.",
                ),
                io.Boolean.Input(
                    "loop_trim_start",
                    default=False,
                    label_on="trim start",
                    label_off="keep start",
                    tooltip="Search both the beginning and end for the closest loop pair.",
                ),
                io.Audio.Input("audio", optional=True, tooltip="Optional audio track."),
                io.Custom("PIPE").Input(
                    "pipe_opt",
                    optional=True,
                    tooltip="Optional Generation Data PIPE for metadata and placeholders.",
                ),
            ],
            outputs=[
                io.Image.Output(
                    "images",
                    is_output_list=True,
                    tooltip="Frames after trim or loop processing.",
                )
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
            is_input_list=True,
        )

    @classmethod
    def execute(
        cls,
        images: Any,
        features: Any = None,
        embed_workflow: bool = True,
        save_generation_data: bool = True,
        remove_prompts: bool = False,
        save_workflow_as_json: bool = False,
        add_loras_to_prompt: bool = False,
        enable_trim: bool = True,
        fps: float = 24.0,
        filename_prefix: str = "video/ComfyUI_Eclipse",
        format: str = "mp4",
        codec: str = "h264",
        crf: int = 19,
        preset: str = "veryfast",
        trim_mode: str = "video_to_audio",
        loop_search_pct: int = 50,
        loop_blend_frames: int = 8,
        loop_metric: str = "ncc",
        loop_trim_start: bool = False,
        audio: dict[str, Any] | None = None,
        pipe_opt: Any = None,
    ) -> io.NodeOutput:
        del features, format
        embed_workflow = bool(unwrap_value(embed_workflow, True))
        save_generation_data = bool(unwrap_value(save_generation_data, True))
        remove_prompts = bool(unwrap_value(remove_prompts, False))
        save_workflow_as_json = bool(unwrap_value(save_workflow_as_json, False))
        add_loras_to_prompt = bool(unwrap_value(add_loras_to_prompt, False))
        enable_trim = bool(unwrap_value(enable_trim, True))
        fps = float(unwrap_value(fps, 24.0))
        filename_prefix = str(
            unwrap_value(filename_prefix, "video/ComfyUI_Eclipse")
        )
        codec = str(unwrap_value(codec, "h264"))
        crf = int(unwrap_value(crf, 19))
        preset = str(unwrap_value(preset, "veryfast"))
        trim_mode = str(unwrap_value(trim_mode, "video_to_audio"))
        loop_search_pct = int(unwrap_value(loop_search_pct, 50))
        loop_blend_frames = int(unwrap_value(loop_blend_frames, 8))
        loop_metric = str(unwrap_value(loop_metric, "ncc"))
        loop_trim_start = bool(unwrap_value(loop_trim_start, False))
        audio = unwrap_value(audio, None)
        context = _unwrap_pipe(pipe_opt)
        if not enable_trim:
            trim_mode = "none"

        if images is None:
            return io.NodeOutput(None, ui={"eclipse_video": []})
        flat_images = flatten_images(images)
        if not flat_images:
            return io.NodeOutput(None, ui={"eclipse_video": []})

        was_batch = was_input_batch(images)
        input_batch = single_input_batch(images)
        is_loop_mode = trim_mode in ("loop_match", "loop_match_blend")
        images_tensor = None
        frames = flat_images
        if is_loop_mode:
            images_tensor = input_batch
            if images_tensor is None:
                images_tensor = cat_and_fit_images(
                    flat_images, log_prefix=_LOG_PREFIX
                )
            if images_tensor is None:
                return io.NodeOutput(None, ui={"eclipse_video": []})
            frame_count = images_tensor.shape[0]
        else:
            if any(frame.dim() != 4 or frame.shape[0] != 1 for frame in frames):
                log.error(_LOG_PREFIX, "Images must be 3D or 4D IMAGE tensors.")
                return io.NodeOutput(None, ui={"eclipse_video": []})
            frame_count = len(frames)

        if audio is not None and trim_mode not in (
            "none",
            "loop_match",
            "loop_match_blend",
        ):
            try:
                waveform = audio.get("waveform")
                sample_rate = int(audio.get("sample_rate", 0))
                if waveform is not None and sample_rate > 0:
                    channels = waveform[0] if waveform.ndim == 3 else waveform
                    audio_samples = int(channels.shape[-1])
                    audio_frames = max(
                        1, math.floor((audio_samples / float(sample_rate)) * fps)
                    )
                    target_frames = frame_count
                    target_samples = audio_samples
                    if trim_mode == "video_to_audio":
                        target_frames = min(frame_count, audio_frames)
                    elif trim_mode == "audio_to_video":
                        target_samples = min(
                            audio_samples,
                            _audio_samples_for_video(frame_count, fps, sample_rate),
                        )
                    elif trim_mode == "shortest":
                        target_frames = min(frame_count, audio_frames)
                        target_samples = min(
                            audio_samples,
                            _audio_samples_for_video(target_frames, fps, sample_rate),
                        )
                    if target_frames < frame_count:
                        if images_tensor is not None:
                            images_tensor = images_tensor[:target_frames]
                        else:
                            frames = frames[:target_frames]
                        frame_count = target_frames
                    if target_samples < audio_samples:
                        trimmed = waveform[..., :target_samples]
                        audio = {"waveform": trimmed, "sample_rate": sample_rate}
            except Exception as error:  # noqa: BLE001 - preserve save-as-is behavior
                log.warning(_LOG_PREFIX, f"Trim alignment failed, saving as-is: {error}")

        if is_loop_mode and frame_count >= 4 and images_tensor is not None:
            try:
                if loop_trim_start:
                    head_index, tail_index, score = _find_loop_pair(
                        images_tensor, loop_search_pct, loop_metric
                    )
                else:
                    head_index = 0
                    tail_index, score = _find_loop_point(
                        images_tensor, loop_search_pct, loop_metric
                    )
                log.msg(
                    _LOG_PREFIX,
                    f"Loop range: {head_index}-{tail_index}, {loop_metric}={score:.5f}",
                )
                images_tensor = images_tensor[head_index : tail_index + 1]
                frame_count = images_tensor.shape[0]
                if (
                    trim_mode == "loop_match_blend"
                    and loop_blend_frames > 0
                    and frame_count > loop_blend_frames * 2
                ):
                    blend_count = min(loop_blend_frames, frame_count // 4)
                    blended = images_tensor.clone()
                    for index in range(blend_count):
                        amount = (index + 1) / (blend_count + 1)
                        tail = frame_count - blend_count + index
                        blended[tail] = (
                            images_tensor[tail] * (1.0 - amount)
                            + images_tensor[index] * amount
                        )
                    images_tensor = blended
                if isinstance(audio, dict):
                    sample_rate = int(audio.get("sample_rate", 0))
                    waveform = audio.get("waveform")
                    if waveform is not None and sample_rate > 0:
                        target_samples = _audio_samples_for_video(
                            frame_count, fps, sample_rate
                        )
                        if waveform.shape[-1] > target_samples:
                            audio = {
                                "waveform": waveform[..., :target_samples],
                                "sample_rate": sample_rate,
                            }
            except Exception as error:  # noqa: BLE001 - preserve save-as-is behavior
                log.warning(_LOG_PREFIX, f"Loop detection failed, saving as-is: {error}")

        if images_tensor is not None:
            height, width = images_tensor.shape[-3:-1]
            images_to_encode = images_tensor
            images_out = prepare_image_output(images_tensor, was_batch)
        else:
            height, width = frames[0].shape[1:3]
            images_to_encode = frames
            mismatched = _has_mismatched_frame_size(frames, height, width)
            if input_batch is not None and not mismatched:
                output_batch = input_batch[:frame_count]
                images_out = prepare_image_output(output_batch, True)
            elif mismatched:
                images_out = [_fit_frame(frame, height, width) for frame in frames]
            else:
                images_out = frames

        output_root = folder_paths.get_output_directory()
        try:
            save_directory, filename_stem, subfolder = _resolve_filename_prefix(
                output_root, filename_prefix, _placeholder_values(context)
            )
        except Exception as error:  # noqa: BLE001 - normalize all invalid paths for the UI
            log.error(_LOG_PREFIX, f"Invalid filename prefix: {error}")
            return io.NodeOutput(images_out, ui={"eclipse_video": []})
        counter = _next_counter(save_directory, filename_stem)
        filename = _video_filename(filename_stem, counter)
        output_path = os.path.join(save_directory, filename)

        metadata = _metadata(
            cls.hidden.prompt,
            cls.hidden.extra_pnginfo,
            context,
            embed_workflow,
            save_generation_data,
            remove_prompts,
            add_loras_to_prompt,
            width,
            height,
        )
        try:
            _encode(
                images_to_encode,
                fps,
                audio,
                output_path,
                codec,
                crf,
                preset,
                metadata,
                height,
                width,
            )
            if save_workflow_as_json:
                _save_workflow_json(
                    cls.hidden.extra_pnginfo, os.path.splitext(output_path)[0]
                )
            log.msg(_LOG_PREFIX, f"Video saved to: {output_path}")
        except Exception as error:  # noqa: BLE001 - encoder failures return an empty preview
            log.error(_LOG_PREFIX, f"Failed to save video: {error}")
            return io.NodeOutput(images_out, ui={"eclipse_video": []})

        result = {
            "filename": filename,
            "subfolder": subfolder,
            "type": "output",
            "format": "video/mp4",
            "frame_rate": fps,
        }
        return io.NodeOutput(images_out, ui={"eclipse_video": [result]})
