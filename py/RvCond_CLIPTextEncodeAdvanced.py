import torch #type: ignore
from typing import Any, List, Optional, Sequence
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

PRESET_WEIGHTS: dict = {
    "balanced": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.5, 5.0, 1.1, 4.0, 1.0],
    "detail": [0.8, 0.8, 0.9, 0.9, 1.0, 1.0, 1.2, 3.0, 6.0, 1.5, 5.0, 1.2],
    "subtle": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.5, 2.0, 1.0, 1.5, 1.0],
    "uniform": [1.0] * 12,
}

def parse_weights(text: str) -> List[float]:
    if text is None or not text.strip():
        raise ValueError("per_layer_weights is empty.")
    parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip() != ""]
    try:
        vals = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"per_layer_weights has a non-numeric entry: {exc}") from exc
    if len(vals) < 2:
        raise ValueError("per_layer_weights needs at least 2 values.")
    return vals

def _rms(t: torch.Tensor) -> torch.Tensor:
    return t.pow(2).mean(dim=tuple(range(1, t.dim()))).sqrt()

def _scale_cond_tensor(
    t: torch.Tensor,
    multiplier: float,
    per_layer_weights: Optional[Sequence[float]] = None,
    renormalize: bool = False,
) -> torch.Tensor:
    if per_layer_weights is None or len(per_layer_weights) <= 1:
        return t * multiplier

    flat = t.shape[-1]
    n_layers = len(per_layer_weights)
    if flat % n_layers != 0:
        log.warning("CLIPTextEncodeAdvanced", f"Conditioning shape {flat} is not divisible by weight count {n_layers}. Falling back to uniform scale.")
        return t * multiplier

    orig_dtype = t.dtype
    ref_rms = _rms(t.float()) if renormalize else None
    t = t.float()
    t = t.view(*t.shape[:-1], n_layers, flat // n_layers)
    gains = torch.tensor(list(per_layer_weights), dtype=t.dtype, device=t.device)
    t = t * gains.view(*([1] * (t.dim() - 2)), n_layers, 1)
    t = t.view(*t.shape[:-2], flat)

    if renormalize and ref_rms is not None:
        new_rms = _rms(t).clamp_min(1e-8)
        t = t * (ref_rms / new_rms).view(-1, *([1] * (t.dim() - 1)))

    return t.to(orig_dtype) * multiplier

def scale_conditioning(
    structure: Any,
    multiplier: float,
    per_layer_weights: Optional[Sequence[float]] = None,
    renormalize: bool = False,
) -> Any:
    if isinstance(structure, list):
        out = []
        for item in structure:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], torch.Tensor)
                and isinstance(item[1], dict)
            ):
                cond_t, extras = item
                out.append(
                    [
                        _scale_cond_tensor(cond_t, multiplier, per_layer_weights, renormalize),
                        dict(extras),
                    ]
                )
            else:
                out.append(
                    scale_conditioning(item, multiplier, per_layer_weights, renormalize)
                )
        return out
    if isinstance(structure, torch.Tensor):
        return _scale_cond_tensor(structure, multiplier, per_layer_weights, renormalize)
    if isinstance(structure, dict):
        return {
            k: scale_conditioning(v, multiplier, per_layer_weights, renormalize)
            for k, v in structure.items()
        }
    return structure


class RvCond_CLIPTextEncodeAdvanced(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CLIP Text Encode (Advanced) [Eclipse]",
            display_name="CLIP Text Encode (Advanced)",
            category=CATEGORY.MAIN.value + CATEGORY.CONDITIONING.value,
            description="Advanced text encoding with support for multi-layer tap rebalancing (Krea2) and global multiplier controls.",
            inputs=[
                io.Clip.Input("clip", tooltip="The input CLIP model used to tokenize and encode the prompt."),
                io.String.Input("text", force_input=True, tooltip="The text prompt to be converted into conditioning embeddings."),
                io.Combo.Input("rebalance_preset", options=["none", "balanced", "detail", "subtle", "uniform", "custom"], default="none",
                               tooltip="Select layer-gain rebalancing profile for Krea2 multi-layer tap models. 'none' disables rebalancing; 'balanced' boosts late taps (up to 5x); 'detail' dampens early and aggressively boosts late taps (up to 6x); 'subtle' gently boosts late taps (up to 2x); 'uniform' applies a flat 1.0; 'custom' uses 'per_layer_weights'."),
                io.String.Input("per_layer_weights", default=", ".join(str(w) for w in PRESET_WEIGHTS["balanced"]),
                                tooltip="Comma-separated float gains (e.g. 12 values for Krea2) applied to individual layer outputs. Active only when preset = 'custom'."),
                io.Float.Input("multiplier", default=1.0, min=-1000.0, max=1000.0, step=0.01,
                               tooltip="Global strength multiplier applied to the conditioning embedding tensor. Values > 1.0 amplify prompt influence; < 1.0 reduce it."),
                io.Boolean.Input("renormalize", default=True,
                                 tooltip="When enabled, rescales the altered conditioning tensor's RMS to match the original, keeping the overall prompt strength/volume constant after layer weighting."),
                io.Boolean.Input("krea2_only_multiplier", default=False,
                                 tooltip="When enabled, the global multiplier is only applied to Krea2 models. For standard models, the multiplier is ignored (treated as 1.0)."),
            ],
            outputs=[
                io.Conditioning.Output("conditioning", tooltip="A conditioning containing the embedded text used to guide the diffusion model."),
            ],
        )

    @classmethod
    def execute(cls, clip, text, rebalance_preset="none", per_layer_weights="", multiplier=1.0, renormalize=True, krea2_only_multiplier=False):
        if clip is None:
            raise RuntimeError("ERROR: clip input is invalid: None\n\nIf the clip is from a checkpoint loader node your checkpoint does not contain a valid clip or text encoder model.")
        
        # 1. Encode text via ComfyUI CLIP
        tokens = clip.tokenize(text)
        conditioning = clip.encode_from_tokens_scheduled(tokens)

        # Check if is_krea2
        is_krea2 = False
        try:
            import comfy.text_encoders.krea2 as krea2 #type: ignore
            is_krea2 = isinstance(clip.cond_stage_model, krea2.Krea2TEModel)
        except Exception:
            pass

        # Adjust multiplier based on Krea2-only setting
        effective_multiplier = 1.0 if (krea2_only_multiplier and not is_krea2) else multiplier

        # 2. Check if rebalancing/scaling is needed
        if rebalance_preset != "none":
            if not is_krea2:
                log.warning("CLIPTextEncodeAdvanced", "Rebalancing is only supported for Krea2 models. Skipping rebalance and applying global multiplier only.")
                if effective_multiplier != 1.0:
                    conditioning = scale_conditioning(conditioning, effective_multiplier, None, False)
            else:
                if rebalance_preset == "custom":
                    weights = parse_weights(per_layer_weights)
                else:
                    weights = PRESET_WEIGHTS.get(rebalance_preset, PRESET_WEIGHTS["balanced"])
                conditioning = scale_conditioning(conditioning, effective_multiplier, weights, renormalize)
        elif effective_multiplier != 1.0:
            conditioning = scale_conditioning(conditioning, effective_multiplier, None, False)

        return io.NodeOutput(conditioning)
