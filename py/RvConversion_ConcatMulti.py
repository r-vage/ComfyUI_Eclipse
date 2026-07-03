import torch #type: ignore
import torch.nn.functional as F #type: ignore
from typing import Any, Dict
from ..core import CATEGORY
from ..core.logger import log
from comfy_api.latest import io #type: ignore

_LOG_PREFIX = "ConcatMulti"

# keys whose values are tensors — merge via torch.cat, never wrap in lists
_TENSOR_KEYS = {
    "image",
    "images",
    "images_ref",
    "images_pp",
    "images_pp1",
    "images_pp2",
    "images_pp3",
    "mask",
    "mask_1",
    "mask_2",
}

# keys that should be treated as list-like when merging
_KNOWN_LIST_KEYS = {
    "audio_in",
    "audio_out",
    "lora_names", 
    "loras", 
    "embeddings", 
    "positive_list", 
    "negative_list"
}

def _is_tensor(v) -> bool:
    return isinstance(v, torch.Tensor)


def _is_empty_value(value) -> bool:
    # Check if a value should be considered empty/invalid for merging
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ('', 'None', 'none', 'null', 'NULL')
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _match_spatial(src: torch.Tensor, target: torch.Tensor, mode: str) -> torch.Tensor:
    # Adapt src tensor to match target's spatial dims.
    # ComfyUI images are [B,H,W,C], masks are [B,H,W].
    t_h, t_w = target.shape[1], target.shape[2]
    if mode == "match":
        return _resize(src, t_h, t_w)
    elif mode == "crop":
        return _center_crop(src, t_h, t_w)
    elif mode == "letterbox":
        return _letterbox(src, t_h, t_w)
    return src


def _resize(src: torch.Tensor, t_h: int, t_w: int) -> torch.Tensor:
    if src.dim() == 4:
        x = src.permute(0, 3, 1, 2).float()
        x = F.interpolate(x, size=(t_h, t_w), mode="bilinear", align_corners=False)
        return x.permute(0, 2, 3, 1).to(src.dtype)
    elif src.dim() == 3:
        x = src.unsqueeze(1).float()
        x = F.interpolate(x, size=(t_h, t_w), mode="bilinear", align_corners=False)
        return x.squeeze(1).to(src.dtype)
    return src


def _center_crop(src: torch.Tensor, t_h: int, t_w: int) -> torch.Tensor:
    # Center-crop or center-pad to target dims.
    if src.dim() == 4:
        s_h, s_w = src.shape[1], src.shape[2]
        # Crop
        y0 = max(0, (s_h - t_h) // 2)
        x0 = max(0, (s_w - t_w) // 2)
        cropped = src[:, y0:y0 + min(s_h, t_h), x0:x0 + min(s_w, t_w), :]
        # If src is smaller than target, pad with black
        if s_h < t_h or s_w < t_w:
            out = torch.zeros(src.shape[0], t_h, t_w, src.shape[3], dtype=src.dtype, device=src.device)
            py = max(0, (t_h - s_h) // 2)
            px = max(0, (t_w - s_w) // 2)
            out[:, py:py + cropped.shape[1], px:px + cropped.shape[2], :] = cropped
            return out
        return cropped
    elif src.dim() == 3:
        s_h, s_w = src.shape[1], src.shape[2]
        y0 = max(0, (s_h - t_h) // 2)
        x0 = max(0, (s_w - t_w) // 2)
        cropped = src[:, y0:y0 + min(s_h, t_h), x0:x0 + min(s_w, t_w)]
        if s_h < t_h or s_w < t_w:
            out = torch.zeros(src.shape[0], t_h, t_w, dtype=src.dtype, device=src.device)
            py = max(0, (t_h - s_h) // 2)
            px = max(0, (t_w - s_w) // 2)
            out[:, py:py + cropped.shape[1], px:px + cropped.shape[2]] = cropped
            return out
        return cropped
    return src


def _letterbox(src: torch.Tensor, t_h: int, t_w: int) -> torch.Tensor:
    # Scale to fit inside target dims (preserve aspect ratio), pad remainder with black.
    if src.dim() == 4:
        s_h, s_w = src.shape[1], src.shape[2]
        scale = min(t_h / s_h, t_w / s_w)
        new_h, new_w = int(s_h * scale), int(s_w * scale)
        x = src.permute(0, 3, 1, 2).float()
        x = F.interpolate(x, size=(new_h, new_w), mode="bilinear", align_corners=False)
        x = x.permute(0, 2, 3, 1).to(src.dtype)
        out = torch.zeros(src.shape[0], t_h, t_w, src.shape[3], dtype=src.dtype, device=src.device)
        py = (t_h - new_h) // 2
        px = (t_w - new_w) // 2
        out[:, py:py + new_h, px:px + new_w, :] = x
        return out
    elif src.dim() == 3:
        s_h, s_w = src.shape[1], src.shape[2]
        scale = min(t_h / s_h, t_w / s_w)
        new_h, new_w = int(s_h * scale), int(s_w * scale)
        x = src.unsqueeze(1).float()
        x = F.interpolate(x, size=(new_h, new_w), mode="bilinear", align_corners=False)
        x = x.squeeze(1).to(src.dtype)
        out = torch.zeros(src.shape[0], t_h, t_w, dtype=src.dtype, device=src.device)
        py = (t_h - new_h) // 2
        px = (t_w - new_w) // 2
        out[:, py:py + new_h, px:px + new_w] = x
        return out
    return src


class RvConversion_ConcatMulti(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Concat Pipe Multi [Eclipse]",
            display_name="Concat Pipe Multi",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            description="Merge multiple pipe/context inputs into a single context dict pipe.",
            inputs=[
                io.Int.Input("inputcount", default=2, min=2, max=64, step=1, socketless=True),
                io.Custom("PIPE").Input("pipe_1", optional=True),
                io.Custom("PIPE").Input("pipe_2", optional=True),
                io.Combo.Input("merge_strategy", options=["overwrite", "preserve", "merge"],
                    default="merge", optional=True,
                    tooltip="How to handle conflicting keys:\n"
                            "'overwrite' replaces earlier values,\n"
                            "'preserve' keeps first valid values,\n"
                            "'merge' combines lists and uses later values for conflicts"),
                io.Combo.Input("tensor_size_mismatch", options=["match", "crop", "letterbox", "ignore"],
                    default="letterbox", optional=True,
                    tooltip="When merging image/mask tensors with different sizes:\n"
                            "'match' resizes (stretch) to the first image's dimensions,\n"
                            "'crop' center-crops to the first image's dimensions,\n"
                            "'letterbox' scales to fit preserving aspect ratio and pads with black,\n"
                            "'ignore' stores them as a list (requires downstream support)"),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, inputcount: int = 2, merge_strategy: str = "merge", tensor_size_mismatch: str = "letterbox", **kwargs) -> io.NodeOutput:
        result: Dict[str, Any] = {}

        aliases = {
            "Steps": "steps",
            "CFG": "cfg",
            "model_name": "model_name",
            "lora_names": "lora_names",
            "loras": "lora_names",
            "seed": "seed",
            "sampler_name": "sampler_name",
            "sampler": "sampler_name",
            "vae_name": "vae_name",
            "directory": "path",
        }

        def set_value(k, v):
            if merge_strategy == "preserve":
                if k in result and not _is_empty_value(result[k]):
                    return
                result[k] = v
                return

            if merge_strategy == "merge":
                if k in result:
                    existing = result[k]
                    # Tensor keys: concatenate along batch dim with torch.cat
                    if k in _TENSOR_KEYS and _is_tensor(existing) and _is_tensor(v):
                        if existing.shape[1:] == v.shape[1:]:
                            result[k] = torch.cat([existing, v], dim=0)
                        elif tensor_size_mismatch != "ignore":
                            # Resize/crop/letterbox v to match existing spatial dims, then cat
                            result[k] = torch.cat([existing, _match_spatial(v, existing, tensor_size_mismatch)], dim=0)
                        else:
                            # 'ignore' — store as list so downstream can handle individually
                            if isinstance(existing, list):
                                existing.append(v)
                            else:
                                result[k] = [existing, v]
                        return
                    if k in _KNOWN_LIST_KEYS and isinstance(existing, str) and isinstance(v, str):
                        result[k] = existing + ", " + v
                        return
                    if isinstance(existing, (list, tuple)) or isinstance(v, (list, tuple)) or k in _KNOWN_LIST_KEYS:
                        existing_list = list(existing) if not isinstance(existing, list) else existing
                        new_list = list(v) if isinstance(v, (list, tuple)) else [v]
                        result[k] = existing_list + new_list
                        return
                result[k] = v
                return

            result[k] = v

        for idx in range(1, inputcount + 1):
            pipe = kwargs.get(f"pipe_{idx}")
            if pipe is None:
                log.debug(_LOG_PREFIX, f"pipe_{idx}: not connected (None)")
                continue

            if isinstance(pipe, tuple):
                ctx = pipe[0] if pipe and isinstance(pipe[0], dict) else {}
            elif isinstance(pipe, dict):
                ctx = pipe
            else:
                raise ValueError(
                    f"Pipe input pipe_{idx} must be a dict or tuple containing a dict. Got: {type(pipe)}"
                )

            non_empty_keys = [k for k, v in ctx.items() if not _is_empty_value(v)]
            log.debug(_LOG_PREFIX, f"pipe_{idx}: connected — {len(ctx)} keys, {len(non_empty_keys)} non-empty {non_empty_keys}")

            for k, v in ctx.items():
                if _is_empty_value(v):
                    continue
                key = aliases.get(k, k)
                # Only wrap in list for true list keys, never for tensors
                if merge_strategy == "merge" and key in _KNOWN_LIST_KEYS and not isinstance(v, (list, tuple)) and not isinstance(v, str) and not _is_tensor(v):
                    v = [v]
                set_value(key, v)

        if "pipe" not in result:
            result["pipe"] = result

        return io.NodeOutput(result)
