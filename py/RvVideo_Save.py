# Save Video [Eclipse]
#
# Eclipse-flavoured replacement for ComfyUI's built-in SaveVideo with two
# improvements:
#   1. Accepts an IMAGE batch + optional AUDIO + fps directly (built-in needs a
#      VIDEO type) so the node can be wired straight from any generator.
#   2. Adds a `trim_mode` widget that aligns video and audio durations before
#      writing the file (built-in just muxes whatever it gets).
#
# Container/codec match the built-in: mp4 / h264. crf is exposed for quality.

import os
import json
import math
import shutil
from fractions import Fraction
from typing import Optional

import av  # type: ignore
import torch  # type: ignore
import folder_paths  # type: ignore
from comfy.cli_args import args  # type: ignore

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.common import resolve_date_tokens
from ..core.logger import log

_LOG_PREFIX = "SaveVideo"


def _frames_for_audio(audio, fps: float) -> Optional[int]:
    try:
        wf = audio.get("waveform")
        sr = int(audio.get("sample_rate", 0))
        if wf is None or sr <= 0:
            return None
        if wf.ndim == 3:
            wf = wf[0]
        return max(1, int(math.floor((wf.shape[-1] / float(sr)) * fps)))
    except Exception:
        return None


def _audio_samples_for_video(num_frames: int, fps: float, sample_rate: int) -> int:
    return max(1, round(num_frames * sample_rate / fps))


# Longest-side pixel size used when downsampling frames before loop-point comparison.
# 512 px gives high spatial fidelity for MSE matching at negligible memory cost.
_LOOP_DOWNSAMPLE_SIZE = 512


_LOOP_METRICS = ["ncc", "mse", "luminance_mse", "gradient_mse"]


def _find_loop_point(images: torch.Tensor, search_pct: int, metric: str = "ncc",
                     ref_idx: int = 0, search_tail: bool = True) -> tuple:
    # Find the frame in a search window that best matches the reference frame.
    #
    # search_tail=True  → scan the last search_pct% of frames against images[ref_idx]
    # search_tail=False → scan the first search_pct% of frames (skipping ref_idx)
    #                     against images[ref_idx] (typically the tail cut-point)
    #
    # Frames are downsampled to _LOOP_DOWNSAMPLE_SIZE px (longest side) before
    # comparison so the operation is fast even for large HD batches.
    #
    # Metrics:
    #   mse           — mean squared pixel error (lower = better)
    #   ncc           — normalized cross-correlation (higher = better); invariant
    #                   to per-frame brightness scale and offset — best for color drift
    #   luminance_mse — BT.601 grayscale MSE; ignores hue/saturation differences
    #   gradient_mse  — edge-magnitude MSE; ignores color entirely, matches structure
    #
    # Returns:
    #     (best_frame_index: int, score: float)
    n = images.shape[0]
    search_n = max(1, round(n * max(1, min(99, search_pct)) / 100.0))

    if search_tail:
        search_start = max(1, n - search_n)
        search_end = n
    else:
        # Head search: start from 1 (or the frame after ref_idx) up to search_n frames in
        search_start = 1 if ref_idx != 1 else 2
        search_end = min(n - 1, 1 + search_n)
        if search_end <= search_start:
            return 0, 0.0  # nothing to search — no-op

    h, w = images.shape[1], images.shape[2]
    scale = _LOOP_DOWNSAMPLE_SIZE / max(h, w)
    ds_h = max(1, round(h * scale))
    ds_w = max(1, round(w * scale))

    F = torch.nn.functional

    # [K, 3, ds_h, ds_w] candidates and [1, 3, ds_h, ds_w] reference — RGB only
    candidates = images[search_start:search_end, ..., :3].permute(0, 3, 1, 2).float()
    reference = images[ref_idx:ref_idx + 1, ..., :3].permute(0, 3, 1, 2).float()
    candidates_ds = F.interpolate(candidates, size=(ds_h, ds_w), mode="bilinear", align_corners=False)
    ref_ds = F.interpolate(reference, size=(ds_h, ds_w), mode="bilinear", align_corners=False)

    if metric == "ncc":
        # Normalized cross-correlation — invariant to brightness scale/offset.
        # Higher score = better match (max 1.0).
        K = candidates_ds.shape[0]
        c_flat = candidates_ds.reshape(K, -1)
        f_flat = ref_ds.reshape(1, -1)
        c_flat = c_flat - c_flat.mean(dim=1, keepdim=True)
        f_flat = f_flat - f_flat.mean()
        scores = (c_flat * f_flat).sum(dim=1) / (c_flat.norm(dim=1) * f_flat.norm() + 1e-8)
        best_local = int(scores.argmax().item())

    elif metric == "luminance_mse":
        # Convert to BT.601 luma before MSE — ignores hue/saturation drift.
        luma_w = torch.tensor([0.299, 0.587, 0.114], device=candidates_ds.device).view(1, 3, 1, 1)
        cand_luma = (candidates_ds * luma_w).sum(dim=1)   # [K, ds_h, ds_w]
        ref_luma = (ref_ds * luma_w).sum(dim=1)            # [1, ds_h, ds_w]
        scores = (cand_luma - ref_luma).pow(2).mean(dim=(1, 2))
        best_local = int(scores.argmin().item())

    elif metric == "gradient_mse":
        # Compare edge-magnitude maps — color-blind, structure-focused.
        def _grad_mag(img: torch.Tensor) -> torch.Tensor:
            # img: [N, 3, H, W] → [N, H-1, W-1] mean gradient magnitude
            dx = img[:, :, :, 1:] - img[:, :, :, :-1]   # [N, 3, H, W-1]
            dy = img[:, :, 1:, :] - img[:, :, :-1, :]   # [N, 3, H-1, W]
            mag = (dx[:, :, :-1, :].pow(2) + dy[:, :, :, :-1].pow(2)).sqrt()
            return mag.mean(dim=1)                        # average over channels
        cand_g = _grad_mag(candidates_ds)   # [K, ds_h-1, ds_w-1]
        ref_g = _grad_mag(ref_ds)            # [1, ds_h-1, ds_w-1]
        scores = (cand_g - ref_g).pow(2).mean(dim=(1, 2))
        best_local = int(scores.argmin().item())

    else:  # mse (default fallback)
        scores = (candidates_ds - ref_ds).pow(2).mean(dim=(1, 2, 3))
        best_local = int(scores.argmin().item())

    return search_start + best_local, float(scores[best_local].item())


_H264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]


def _find_loop_pair(images: torch.Tensor, search_pct: int, metric: str = "ncc") -> tuple:
    # Scan both the first and last search_pct% windows simultaneously and return
    # the (head_idx, tail_idx) pair whose frames are most similar to each other.
    #
    # The windows are capped at n//2 each to prevent overlap. The resulting slice
    # images[head_idx : tail_idx+1] forms the tightest possible loop.
    #
    # Pairwise scores are computed via the identity
    #   ||a-b||^2 = ||a||^2 + ||b||^2 - 2(a·b)
    # so only [H,N] and [T,N] matrices are held in memory, not [H,T,N].
    # NCC uses the same trick through a direct matmul.
    #
    # Returns:
    #     (head_frame_index: int, tail_frame_index: int, score: float)
    n = images.shape[0]
    search_n = max(1, round(n * max(1, min(99, search_pct)) / 100.0))

    # Cap each window at half the sequence so they never overlap
    head_end = min(search_n, n // 2)          # head window: [0, head_end)
    tail_start = max(n - search_n, n // 2)    # tail window: [tail_start, n)
    if head_end < 1 or tail_start >= n or head_end > tail_start:
        return 0, n - 1, 0.0  # nothing sensible to search — no-op

    h, w = images.shape[1], images.shape[2]
    scale = _LOOP_DOWNSAMPLE_SIZE / max(h, w)
    ds_h = max(1, round(h * scale))
    ds_w = max(1, round(w * scale))

    F = torch.nn.functional
    head_ds = F.interpolate(
        images[:head_end, ..., :3].permute(0, 3, 1, 2).float(),
        size=(ds_h, ds_w), mode="bilinear", align_corners=False,
    )  # [H, 3, ds_h, ds_w]
    tail_ds = F.interpolate(
        images[tail_start:, ..., :3].permute(0, 3, 1, 2).float(),
        size=(ds_h, ds_w), mode="bilinear", align_corners=False,
    )  # [T, 3, ds_h, ds_w]
    H, T = head_ds.shape[0], tail_ds.shape[0]

    if metric == "ncc":
        h_flat = head_ds.reshape(H, -1)
        t_flat = tail_ds.reshape(T, -1)
        h_norm = h_flat - h_flat.mean(dim=1, keepdim=True)
        t_norm = t_flat - t_flat.mean(dim=1, keepdim=True)
        # [H, T] pairwise NCC (higher = better)
        sim = (h_norm @ t_norm.T) / (
            h_norm.norm(dim=1, keepdim=True) * t_norm.norm(dim=1).unsqueeze(0) + 1e-8
        )
        flat_best = int(sim.argmax().item())
        best_h, best_t = flat_best // T, flat_best % T
        score = float(sim[best_h, best_t].item())

    else:
        # For MSE-family: ||a-b||^2 = ||a||^2 + ||b||^2 - 2(a·b), lower = better
        if metric == "luminance_mse":
            luma_w = torch.tensor([0.299, 0.587, 0.114], device=head_ds.device).view(1, 3, 1, 1)
            h_feat = (head_ds * luma_w).sum(dim=1).reshape(H, -1)   # [H, N']
            t_feat = (tail_ds * luma_w).sum(dim=1).reshape(T, -1)   # [T, N']
        elif metric == "gradient_mse":
            def _grad_mag(img: torch.Tensor) -> torch.Tensor:
                dx = img[:, :, :, 1:] - img[:, :, :, :-1]
                dy = img[:, :, 1:, :] - img[:, :, :-1, :]
                return (dx[:, :, :-1, :].pow(2) + dy[:, :, :, :-1].pow(2)).sqrt().mean(dim=1)
            h_feat = _grad_mag(head_ds).reshape(H, -1)
            t_feat = _grad_mag(tail_ds).reshape(T, -1)
        else:  # mse
            h_feat = head_ds.reshape(H, -1)
            t_feat = tail_ds.reshape(T, -1)
        N = h_feat.shape[1]
        h_sq = h_feat.pow(2).sum(dim=1) / N       # [H]
        t_sq = t_feat.pow(2).sum(dim=1) / N       # [T]
        cross = (h_feat @ t_feat.T) / N           # [H, T]
        scores = h_sq.unsqueeze(1) + t_sq.unsqueeze(0) - 2 * cross  # [H, T]
        flat_best = int(scores.argmin().item())
        best_h, best_t = flat_best // T, flat_best % T
        score = float(scores[best_h, best_t].item())

    return best_h, tail_start + best_t, score


def _encode(images, fps: float, audio, output_path: str, codec: str, crf: int, preset: str, metadata) -> None:
    height = int(images.shape[-3])
    width = int(images.shape[-2])

    container = av.open(output_path, mode="w", options={"movflags": "use_metadata_tags+faststart"})
    if metadata:
        for k, v in metadata.items():
            try:
                container.metadata[k] = json.dumps(v) if not isinstance(v, str) else v
            except Exception:
                pass

    enc_w = width - (width % 2)
    enc_h = height - (height % 2)

    vstream = container.add_stream(codec, rate=Fraction(round(fps * 1000), 1000))
    if not isinstance(vstream, av.VideoStream):
        raise ValueError("Failed to create video stream")
    vstream.width = enc_w
    vstream.height = enc_h
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"crf": str(crf), "preset": preset}

    astream = None
    if audio is not None and isinstance(audio, dict) and "waveform" in audio and "sample_rate" in audio:
        try:
            sample_rate = int(audio["sample_rate"])
            waveform = audio["waveform"]
            if waveform.ndim == 3:
                waveform = waveform[0]
            channels = int(waveform.shape[0])
            astream = container.add_stream("aac", rate=sample_rate)
            astream.layout = "stereo" if channels >= 2 else "mono"
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Audio stream init failed, skipping audio: {e}")
            astream = None

    for frame in images:
        arr = torch.clamp(frame[..., :3] * 255.0, min=0, max=255).to(
            device=torch.device("cpu"), dtype=torch.uint8
        ).numpy()
        if arr.shape[0] != enc_h or arr.shape[1] != enc_w:
            arr = arr[:enc_h, :enc_w, :]
        vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in vstream.encode(vframe):
            container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)

    if astream is not None:
        try:
            wf = audio["waveform"]
            if wf.ndim == 3:
                wf = wf[0]
            sample_rate = int(audio["sample_rate"])
            np_audio = wf.detach().to(device=torch.device("cpu"), dtype=torch.float32).contiguous().numpy()
            aframe = av.AudioFrame.from_ndarray(
                np_audio,
                format="fltp",
                layout="stereo" if np_audio.shape[0] >= 2 else "mono",
            )
            aframe.sample_rate = sample_rate
            for packet in astream.encode(aframe):
                container.mux(packet)
            for packet in astream.encode():
                container.mux(packet)
        except Exception as e:
            log.warning(_LOG_PREFIX, f"Audio encode failed (video still saved): {e}")

    container.close()


class RvVideo_Save(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Save Video [Eclipse]",
            display_name="Save Video",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description=(
                "Saves an IMAGE batch (+ optional AUDIO) to an mp4 in the output folder. "
                "`trim_mode` aligns video/audio length before writing: "
                "`video_to_audio` shortens the frame batch to the audio duration, "
                "`audio_to_video` shortens the audio to the frame batch length, "
                "`shortest` clips both sides to the shorter duration. "
                "`loop_match` finds the end-frame that best matches frame 0 and trims there; "
                "`loop_match_blend` does the same and then crossfades the tail back to the start "
                "for a seamless loop."
            ),
            inputs=[
                io.Image.Input("images", tooltip="Batch of frames to save."),
                io.Float.Input(
                    "fps", default=24.0, min=1.0, max=240.0, step=0.01,
                    tooltip="Output video frame rate.",
                ),
                io.String.Input(
                    "filename_prefix", default="video/ComfyUI_Eclipse",
                    tooltip="Filename prefix in the output directory.",
                ),
                io.Combo.Input("format", options=["mp4"], default="mp4",
                               tooltip="Output container format."),
                io.Combo.Input("codec", options=["h264"], default="h264",
                               tooltip="Output video codec."),
                io.Int.Input(
                    "crf", default=19, min=0, max=51, step=1,
                    tooltip="Quality factor (lower = higher quality, larger file). 18–23 is a good range.",
                ),
                io.Combo.Input(
                    "preset", options=_H264_PRESETS, default="veryfast",
                    tooltip="Compression preset. Does not affect visual quality (controlled by CRF). Faster presets encode quicker but produce larger files; slower presets compress better at the same CRF.",
                ),
                io.Combo.Input(
                    "trim_mode",
                    options=[
                        "none",
                        "video_to_audio", "audio_to_video", "shortest",
                        "loop_match", "loop_match_blend",
                    ],
                    default="video_to_audio",
                    tooltip=(
                        "Align frame batch and audio duration before saving (ignored when no audio). "
                        "loop_match: trim to the end-frame closest to frame 0 for a seamless loop. "
                        "loop_match_blend: same as loop_match but crossfades the tail into the start."
                    ),
                ),
                io.Int.Input(
                    "loop_search_pct", default=50, min=1, max=99, step=1,
                    tooltip=(
                        "Percentage of the frame batch (tail) to scan when searching for a loop point. "
                        "Only used by loop_match and loop_match_blend modes."
                    ),
                ),
                io.Int.Input(
                    "loop_blend_frames", default=8, min=0, max=60, step=1,
                    tooltip=(
                        "Number of frames to crossfade at the end of the loop. "
                        "Only used by loop_match_blend mode. 0 disables blending."
                    ),
                ),
                io.Combo.Input(
                    "loop_metric", options=_LOOP_METRICS, default="ncc",
                    tooltip=(
                        "Similarity metric used to find the best loop point. "
                        "ncc: normalized cross-correlation — invariant to brightness/color drift (recommended). "
                        "mse: raw per-pixel MSE — fast but sensitive to brightness differences. "
                        "luminance_mse: grayscale MSE — ignores hue/saturation shifts. "
                        "gradient_mse: edge-magnitude MSE — color-blind, matches structure only."
                    ),
                ),
                io.Boolean.Input(
                    "loop_trim_start", default=False,
                    label_on="trim start", label_off="keep start",
                    tooltip=(
                        "When enabled, also scans the beginning of the batch for the frame "
                        "that best matches the tail cut-point, then trims the start there. "
                        "Produces a tighter loop when neither end of the clip is a perfect match. "
                        "Only used by loop_match and loop_match_blend modes."
                    ),
                ),
                io.Audio.Input("audio", optional=True, tooltip="Optional audio track to mux."),
            ],
            outputs=[
                io.Image.Output("images", tooltip="The saved frame batch after any trim or loop processing."),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, images, fps: float, filename_prefix: str, format: str, codec: str,
                crf: int, preset: str, trim_mode: str,
                loop_search_pct: int = 50, loop_blend_frames: int = 8,
                loop_metric: str = "ncc", loop_trim_start: bool = False,
                audio: Optional[dict] = None) -> io.NodeOutput:
        if images is None or not hasattr(images, "shape") or images.shape[0] == 0:
            return io.NodeOutput(ui={"eclipse_video": []})

        num_frames = int(images.shape[0])

        # ---- trim alignment (audio/video duration) ----
        if audio is not None and trim_mode not in ("none", "loop_match", "loop_match_blend"):
            try:
                wf = audio.get("waveform")
                sr = int(audio.get("sample_rate", 0))
                if wf is not None and sr > 0:
                    if wf.ndim == 3:
                        wf_ch = wf[0]
                    else:
                        wf_ch = wf
                    audio_samples = int(wf_ch.shape[-1])
                    audio_frames = max(1, math.floor((audio_samples / float(sr)) * fps))
                    target_video_frames = num_frames
                    target_audio_samples = audio_samples

                    if trim_mode == "video_to_audio":
                        target_video_frames = min(num_frames, audio_frames)
                    elif trim_mode == "audio_to_video":
                        target_audio_samples = min(audio_samples, _audio_samples_for_video(num_frames, fps, sr))
                    elif trim_mode == "shortest":
                        target_video_frames = min(num_frames, audio_frames)
                        target_audio_samples = min(audio_samples, _audio_samples_for_video(target_video_frames, fps, sr))

                    if target_video_frames < num_frames:
                        images = images[:target_video_frames]
                        num_frames = target_video_frames
                    if target_audio_samples < audio_samples:
                        new_wf = wf[..., :target_audio_samples] if wf.ndim == 3 else wf_ch[..., :target_audio_samples]
                        audio = {"waveform": new_wf if new_wf.ndim == 3 else new_wf.unsqueeze(0),
                                 "sample_rate": sr}
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Trim alignment failed, saving as-is: {e}")

        # ---- loop detection (loop_match / loop_match_blend) ----
        if trim_mode in ("loop_match", "loop_match_blend") and num_frames >= 4:
            try:
                if loop_trim_start:
                    # Simultaneous pair search: scan both head and tail windows and
                    # find the (head_idx, tail_idx) pair whose frames are most similar.
                    head_idx, tail_idx, pair_score = _find_loop_pair(
                        images, loop_search_pct, loop_metric
                    )
                    log.msg(_LOG_PREFIX,
                            f"Loop pair: head={head_idx}, tail={tail_idx}/{num_frames - 1}, "
                            f"{loop_metric}={pair_score:.5f}")
                else:
                    # Tail-only: find the end-frame closest to frame 0
                    head_idx = 0
                    tail_idx, tail_score = _find_loop_point(
                        images, loop_search_pct, loop_metric, ref_idx=0, search_tail=True
                    )
                    log.msg(_LOG_PREFIX,
                            f"Loop tail: frame {tail_idx}/{num_frames - 1}, {loop_metric}={tail_score:.5f}")

                images = images[head_idx:tail_idx + 1]
                num_frames = int(images.shape[0])

                if trim_mode == "loop_match_blend" and loop_blend_frames > 0 and num_frames > loop_blend_frames * 2:
                    blend_n = min(loop_blend_frames, num_frames // 4)
                    blended = images.clone()
                    for k in range(blend_n):
                        t = (k + 1) / (blend_n + 1)        # ramps 0→1 across the blend window
                        i = num_frames - blend_n + k        # index in the tail
                        blended[i] = images[i] * (1.0 - t) + images[k] * t
                    images = blended

                # Re-trim audio to match the (possibly shorter) loop video
                if audio is not None and isinstance(audio, dict) and "waveform" in audio and "sample_rate" in audio:
                    try:
                        sr = int(audio["sample_rate"])
                        if sr > 0:
                            target_samples = _audio_samples_for_video(num_frames, fps, sr)
                            wf = audio["waveform"]
                            if int(wf.shape[-1]) > target_samples:
                                audio = {"waveform": wf[..., :target_samples], "sample_rate": sr}
                    except Exception:
                        pass
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Loop detection failed, saving as-is: {e}")

        height = int(images.shape[-3])
        width = int(images.shape[-2])

        filename_prefix = resolve_date_tokens(filename_prefix)

        # Detect absolute external path (Linux /... or Windows C:\...)
        _prefix_norm = os.path.normpath(filename_prefix)
        _is_abs = os.path.isabs(_prefix_norm) or (len(_prefix_norm) > 1 and _prefix_norm[1] == ':')

        if _is_abs:
            full_output_folder = os.path.dirname(_prefix_norm)
            filename_stem = os.path.basename(_prefix_norm) or "ComfyUI_Eclipse"
            os.makedirs(full_output_folder, exist_ok=True)
            try:
                prefix_len = len(filename_stem)
                existing = [
                    int(f[prefix_len + 1 : prefix_len + 6])
                    for f in os.listdir(full_output_folder)
                    if f.startswith(filename_stem + "_")
                    and f[prefix_len + 1 : prefix_len + 6].isdigit()
                    and f[prefix_len + 6:] == ".mp4"
                ]
                counter = max(existing) + 1 if existing else 1
            except Exception:
                counter = 1
            file = f"{filename_stem}_{counter:05}.mp4"
            out_path = os.path.join(full_output_folder, file)
            # Encode to ComfyUI temp dir, copy to final destination for preview
            temp_file = f"eclipse_sv_{counter:05}.mp4"
            encode_path = os.path.join(folder_paths.get_temp_directory(), temp_file)
            result_filename = temp_file
            result_subfolder = ""
            result_type = "temp"
        else:
            full_output_folder, filename_stem, counter, subfolder, _ = folder_paths.get_save_image_path(
                filename_prefix, folder_paths.get_output_directory(), width, height
            )
            file = f"{filename_stem}_{counter:05}_.mp4"
            out_path = os.path.join(full_output_folder, file)
            encode_path = out_path
            result_filename = file
            result_subfolder = subfolder
            result_type = "output"

        metadata = None
        if not args.disable_metadata:
            metadata = {}
            if cls.hidden.extra_pnginfo is not None:
                metadata.update(cls.hidden.extra_pnginfo)
            if cls.hidden.prompt is not None:
                metadata["prompt"] = cls.hidden.prompt

        try:
            _encode(images, fps, audio, encode_path, codec=codec, crf=crf, preset=preset, metadata=metadata)
            if _is_abs:
                shutil.copy2(encode_path, out_path)
                log.msg(_LOG_PREFIX, f"Video saved to: {out_path}")
        except Exception as e:
            log.error(_LOG_PREFIX, f"Failed to save video: {e}")
            return io.NodeOutput(ui={"eclipse_video": []})

        result = {
            "filename": result_filename,
            "subfolder": result_subfolder,
            "type": result_type,
            "format": "video/mp4",
            "frame_rate": fps,
        }
        # Custom ui key so the frontend does NOT auto-create a fixed-size preview
        # widget. The JS extension (eclipse-save-video.js) reads this key from
        # onExecuted and renders a resizable DOM <video> instead.
        return io.NodeOutput(images, ui={"eclipse_video": [result]})
