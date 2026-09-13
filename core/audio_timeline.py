"""Shared audio-timeline validation and encoded-activity helpers."""

import math
from typing import Any

import torch  # type: ignore
import torch.nn.functional as F  # type: ignore

ENCODED_ACTIVITY_FPS = 50.0


def audio_duration_seconds(audio: Any) -> float:
    """Return the duration of a validated ComfyUI AUDIO value."""
    if not isinstance(audio, dict):
        # Preserve the published WAN planner's validation exception type.
        raise ValueError(  # noqa: TRY004
            "audio must be a valid ComfyUI AUDIO value."
        )

    waveform = audio.get("waveform")
    sample_rate = audio.get("sample_rate")
    if not isinstance(waveform, torch.Tensor) or waveform.ndim < 1:
        raise ValueError("audio waveform is missing or invalid.")
    if not isinstance(sample_rate, (int, float)) or sample_rate <= 0:
        raise ValueError("audio sample rate must be greater than zero.")

    return waveform.shape[-1] / float(sample_rate)


def encoded_audio_activity(
    audio_encoder_output: Any,
) -> tuple[torch.Tensor | None, str]:
    """Derive a smoothed activity curve from InfiniteTalk Wav2Vec features."""
    if not isinstance(audio_encoder_output, dict):
        return None, "encoder output is missing or is not a dictionary"

    layers = audio_encoder_output.get("encoded_audio_all_layers")
    if not isinstance(layers, (list, tuple)) or len(layers) < 2:
        return None, "encoded_audio_all_layers must contain at least two layers"

    normalized_layers: list[torch.Tensor] = []
    expected_shape: tuple[int, ...] | None = None
    for layer in layers:
        if not isinstance(layer, torch.Tensor):
            return None, "encoded audio layers must be tensors"
        if layer.ndim == 2:
            layer = layer.unsqueeze(0)
        if layer.ndim != 3 or layer.shape[1] < 2 or layer.shape[2] < 1:
            return None, (
                "encoded audio layers must have shape [batch, time, features]"
            )
        current_shape = tuple(layer.shape)
        if expected_shape is None:
            expected_shape = current_shape
        elif current_shape != expected_shape:
            return None, "encoded audio layers do not share one shape"
        normalized_layers.append(layer)

    # InfiniteTalk excludes the first hidden layer. Analyze the final third of
    # the same remaining stack so a connected encoder result can be reused.
    remaining_layers = normalized_layers[1:]
    selected_layers = remaining_layers[(2 * len(remaining_layers)) // 3 :]

    try:
        stacked = torch.stack(selected_layers, dim=0).float()
        cosine = F.cosine_similarity(
            stacked[:, :, 1:, :], stacked[:, :, :-1, :], dim=-1, eps=1e-8
        )
        changes = (1.0 - cosine).clamp_min(0.0).mean(dim=(0, 1))
        activity = torch.cat((changes[:1], changes), dim=0).detach().cpu()

        # Five native 50 Hz samples equal a 100 ms smoothing span.
        padded = F.pad(activity.view(1, 1, -1), (2, 2), mode="replicate")
        smoothed = F.avg_pool1d(padded, kernel_size=5, stride=1).view(-1)
    except (RuntimeError, TypeError, ValueError) as error:
        return None, f"failed to derive encoded activity: {error}"

    if smoothed.numel() < 2 or not torch.isfinite(smoothed).all().item():
        return None, "encoded activity is empty or contains non-finite values"
    return smoothed, ""


def _sustained_activity_start(
    high_mask: torch.Tensor, start_index: int, hold_samples: int
) -> int | None:
    last_start = high_mask.numel() - hold_samples
    for index in range(start_index, last_start + 1):
        if bool(high_mask[index : index + hold_samples].all().item()):
            return index
    return None


def align_activity_gap(
    activity: torch.Tensor,
    ideal_frame: int,
    fps: float,
    transition_edge: str,
    search_window_seconds: float,
    min_silence_duration: float,
    resume_hold_duration: float,
    *,
    minimum_frame: int | None = None,
    maximum_frame: int | None = None,
) -> tuple[int | None, str]:
    """Find a nearby encoded-activity gap and return its requested edge."""
    ideal_sample = ideal_frame / fps * ENCODED_ACTIVITY_FPS
    search_samples = search_window_seconds * ENCODED_ACTIVITY_FPS
    window_start = max(0, math.floor(ideal_sample - search_samples))
    window_end = min(activity.numel(), math.ceil(ideal_sample + search_samples) + 1)
    if minimum_frame is not None:
        minimum_sample = minimum_frame / fps * ENCODED_ACTIVITY_FPS
        window_start = max(window_start, math.ceil(minimum_sample))
    if maximum_frame is not None:
        maximum_sample = maximum_frame / fps * ENCODED_ACTIVITY_FPS
        window_end = min(window_end, math.floor(maximum_sample) + 1)

    local_activity = activity[window_start:window_end]
    if local_activity.numel() < 2:
        return None, "activity search window is empty"

    silence_threshold = torch.quantile(local_activity, 0.30).item()
    resume_threshold = torch.quantile(local_activity, 0.60).item()
    low_mask = local_activity <= silence_threshold
    high_mask = local_activity >= resume_threshold
    silence_samples = max(1, math.ceil(min_silence_duration * ENCODED_ACTIVITY_FPS))
    hold_samples = max(1, math.ceil(resume_hold_duration * ENCODED_ACTIVITY_FPS))

    candidates: list[tuple[int, int, int, float]] = []
    index = 0
    while index < low_mask.numel():
        if not bool(low_mask[index].item()):
            index += 1
            continue

        run_start = index
        while index < low_mask.numel() and bool(low_mask[index].item()):
            index += 1
        run_end = index
        if run_end - run_start < silence_samples:
            continue

        resume_start = _sustained_activity_start(high_mask, run_end, hold_samples)
        if resume_start is None:
            continue

        global_start = window_start + run_start
        global_end = window_start + run_end
        global_resume = window_start + resume_start
        if ideal_sample < global_start:
            interval_distance = global_start - ideal_sample
        elif ideal_sample >= global_end:
            interval_distance = ideal_sample - (global_end - 1)
        else:
            interval_distance = 0.0
        candidates.append((global_start, global_end, global_resume, interval_distance))

    if not candidates:
        return (
            None,
            "no activity gap met both the silence and sustained-resume requirements",
        )

    def candidate_key(candidate: tuple[int, int, int, float]) -> tuple[float, float]:
        selected_sample = (
            candidate[0] if transition_edge == "silence_start" else candidate[2]
        )
        return candidate[3], abs(selected_sample - ideal_sample)

    selected = min(candidates, key=candidate_key)
    selected_sample = selected[0] if transition_edge == "silence_start" else selected[2]
    aligned_frame = round(selected_sample / ENCODED_ACTIVITY_FPS * fps)
    details = (
        f"gap {selected[0] / ENCODED_ACTIVITY_FPS:.3f}s-"
        f"{selected[1] / ENCODED_ACTIVITY_FPS:.3f}s, resume "
        f"{selected[2] / ENCODED_ACTIVITY_FPS:.3f}s, thresholds "
        f"{silence_threshold:.6f}/{resume_threshold:.6f}"
    )
    return aligned_frame, details
