#
# Loop Align Silence [Eclipse]
#
# Analyzes the input audio track to find natural silences/pauses,
# and aligns loop transition indices to occur during those pauses.
# Outputs a comma-separated string of loop indices suitable for Loop Image Selector.
#

import math
import torch  # type: ignore

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "LoopAlignSilence"


class RvAudio_LoopAlignSilence(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Loop Align Silence [Eclipse]",
            display_name="Loop Align Silence",
            description=(
                "Analyzes an audio track to detect natural pauses or silent gaps,\n"
                "and aligns loop transitions (scene changes) to those points.\n"
                "Helps prevent image/singer changes from occurring mid-sentence."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.AUDIO.value,
            inputs=[
                io.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip="Optional AUDIO input. If not connected, manual_targets are parsed directly without alignment.",
                ),
                io.Float.Input(
                    "fps",
                    default=16.0,
                    min=1.0,
                    max=240.0,
                    step=0.01,
                    tooltip="Target frame rate (frames per second) of the video.",
                ),
                io.Int.Input(
                    "context_length",
                    default=81,
                    min=1,
                    max=4096,
                    step=1,
                    tooltip="Loop context length (frame budget per loop iteration).",
                ),
                io.Int.Input(
                    "overlap_frames",
                    default=9,
                    min=0,
                    max=4096,
                    step=1,
                    tooltip="Number of overlapping frames between loops (e.g. motion_frame_count in InfiniteTalkToVideo).",
                ),
                io.Combo.Input(
                    "mode",
                    options=["align_manual_targets", "auto_detect_pauses"],
                    default="align_manual_targets",
                    tooltip="Method to determine transition loops. Manual aligns user targets; Auto finds the best pauses.",
                ),
                io.String.Input(
                    "manual_targets",
                    default="5.0, 10.0, 15.0",
                    tooltip="Comma-separated list of target times (in seconds or loop indices) to align with silences.",
                ),
                io.Combo.Input(
                    "target_unit",
                    options=["seconds", "loops"],
                    default="seconds",
                    tooltip="The unit of the manual_targets input.",
                ),
                io.Boolean.Input(
                    "align_to_silence",
                    default=True,
                    tooltip="If True, aligns transitions to quietest loop boundaries in search window. If False (only applies to 'align_manual_targets' mode), transitions occur exactly at target times/loops.",
                ),
                io.Int.Input(
                    "image_count",
                    default=2,
                    min=2,
                    max=64,
                    step=1,
                    tooltip="Number of images/singers in rotation. Used in auto_detect_pauses to select (image_count - 1) transition points.",
                ),
                io.Int.Input(
                    "search_window",
                    default=1,
                    min=1,
                    max=10,
                    step=1,
                    tooltip="Maximum loops forward/backward to search for a silence around each manual target.",
                ),
                io.Float.Input(
                    "window_duration",
                    default=0.4,
                    min=0.05,
                    max=2.0,
                    step=0.01,
                    tooltip="Duration (in seconds) of the audio window analyzed around each loop boundary.",
                ),
            ],
            outputs=[
                io.String.Output(
                    "loop_positions",
                    tooltip="Comma-separated list of loop indices aligned to audio silence.",
                ),
                io.String.Output(
                    "silence_report",
                    tooltip="Detailed text report of the alignment analysis.",
                ),
            ],
        )

    @classmethod
    def execute(
        cls,
        fps,
        context_length,
        overlap_frames,
        mode,
        manual_targets,
        target_unit,
        image_count,
        search_window,
        window_duration,
        align_to_silence=True,
        audio=None,
    ):
        try:
            # 1. Parse manual targets
            raw_targets = []
            if manual_targets:
                for x in manual_targets.split(","):
                    x = x.strip()
                    if not x:
                        continue
                    try:
                        raw_targets.append(float(x))
                    except ValueError:
                        log.warning(_LOG_PREFIX, f"Ignoring invalid target: {x}")
            raw_targets = sorted(list(set(raw_targets)))

            # 2. Get audio properties if available
            wf = None
            sr = 0
            duration = 0.0
            loop_count = 1
            effective_stride = max(1, context_length - overlap_frames)

            if audio is not None:
                wf = audio.get("waveform")
                sr = int(audio.get("sample_rate", 0))
                if wf is not None and sr > 0:
                    duration = float(wf.shape[-1]) / float(sr)
                    total_frames = int(math.ceil(duration * fps))
                    if total_frames <= context_length:
                        loop_count = 1
                    else:
                        loop_count = 1 + int(
                            math.ceil(
                                (total_frames - context_length) / effective_stride
                            )
                        )
                    loop_count = max(1, loop_count)

            # Fallback if audio is missing, invalid, or alignment is disabled (only for manual targets mode)
            if (
                (not align_to_silence and mode == "align_manual_targets")
                or wf is None
                or sr <= 0
            ):
                if not align_to_silence and mode == "align_manual_targets":
                    log.msg(
                        _LOG_PREFIX,
                        "align_to_silence is False. Parsing manual targets directly without audio alignment.",
                    )
                    report = "align_to_silence is False. Targets parsed directly:\n"
                else:
                    log.msg(
                        _LOG_PREFIX,
                        "No audio input connected or invalid sample rate. Parsing manual targets directly.",
                    )
                    report = "No audio input connected. Targets parsed directly:\n"

                aligned_loops = []
                for target in raw_targets:
                    if target_unit == "seconds":
                        target_frames = target * fps
                        if target_frames <= context_length:
                            loop_idx = 1
                        else:
                            loop_idx = 1 + int(
                                round(
                                    (target_frames - context_length) / effective_stride
                                )
                            )
                    else:
                        loop_idx = round(target)
                    if loop_idx > 0:
                        aligned_loops.append(loop_idx)
                        report += f"  Target: {target} {target_unit} -> Loop index: {loop_idx}\n"
                aligned_loops = sorted(list(set(aligned_loops)))
                loop_positions = ", ".join(str(l) for l in aligned_loops)
                return io.NodeOutput(loop_positions, report)

            # 3. Preprocess audio waveform to mono
            # Shape might be [batch, channels, samples] or [channels, samples]
            if wf.ndim == 3:
                wf = wf[0]
            if wf.ndim == 2:
                mono_wf = wf.mean(dim=0)
            else:
                mono_wf = wf

            num_samples = mono_wf.shape[0]
            win_samples = int(window_duration * sr)

            def get_boundary_rms(k):
                # Calculate boundary frame and time
                if k == 0:
                    boundary_frame = 0
                else:
                    boundary_frame = context_length + (k - 1) * effective_stride
                boundary_time = boundary_frame / fps
                center_sample = int(boundary_time * sr)

                # Window range
                start = max(0, center_sample - win_samples // 2)
                end = min(num_samples, center_sample + win_samples // 2)

                if start >= end:
                    return 0.0

                chunk = mono_wf[start:end]
                rms = torch.sqrt(torch.mean(chunk**2)).item()
                return rms

            # 4. Perform alignment depending on mode
            aligned_loops = []
            report = f"Audio analysis complete (Duration: {duration:.2f}s, total loops: {loop_count})\n"
            report += f"Mode: {mode}\n\n"

            if mode == "align_manual_targets":
                report += "Aligned transitions:\n"
                for target in raw_targets:
                    # Determine target loop index
                    if target_unit == "seconds":
                        target_frames = target * fps
                        if target_frames <= context_length:
                            target_loop = 1
                        else:
                            target_loop = 1 + int(
                                round(
                                    (target_frames - context_length) / effective_stride
                                )
                            )
                        target_seconds = target
                    else:
                        target_loop = round(target)
                        if target_loop <= 1:
                            target_seconds = context_length / fps
                        else:
                            target_seconds = (
                                context_length + (target_loop - 1) * effective_stride
                            ) / fps

                    if target_loop <= 0 or target_loop >= loop_count:
                        report += f"  Target {target} ({target_unit}) is out of video range (skipped).\n"
                        continue

                    # Search neighborhood for quietest loop boundary
                    best_loop = target_loop
                    min_rms = float("inf")

                    start_search = max(1, target_loop - search_window)
                    end_search = min(loop_count - 1, target_loop + search_window)

                    search_details = []
                    for k in range(start_search, end_search + 1):
                        rms = get_boundary_rms(k)
                        boundary_frame = context_length + (k - 1) * effective_stride
                        t_sec = boundary_frame / fps
                        search_details.append((k, t_sec, rms))
                        is_better = False
                        if rms < min_rms - 1e-7:
                            is_better = True
                        elif abs(rms - min_rms) <= 1e-7:
                            if abs(k - target_loop) < abs(best_loop - target_loop):
                                is_better = True
                        if is_better:
                            min_rms = rms
                            best_loop = k

                    aligned_loops.append(best_loop)
                    orig_time = target_seconds
                    boundary_frame = context_length + (best_loop - 1) * effective_stride
                    new_time = boundary_frame / fps
                    report += f"  Target around {orig_time:.2f}s (Loop {target_loop}) -> Aligned to Loop {best_loop} ({new_time:.2f}s) | RMS: {min_rms:.5f}\n"
                    for k, t_s, rms in search_details:
                        marker = "*" if k == best_loop else " "
                        report += (
                            f"    {marker} Loop {k:2d} ({t_s:5.2f}s): RMS = {rms:.5f}\n"
                        )

            else:  # auto_detect_pauses
                # Evaluate RMS at all boundaries
                candidates = []
                for k in range(1, loop_count):
                    rms = get_boundary_rms(k)
                    boundary_frame = context_length + (k - 1) * effective_stride
                    t_sec = boundary_frame / fps
                    candidates.append((k, t_sec, rms))

                # Sort by energy (ascending)
                sorted_candidates = sorted(candidates, key=lambda x: x[2])

                # Select top (image_count - 1) transition points with spacing constraint
                num_transitions_needed = max(1, image_count - 1)
                selected = []

                # We try different minimum spacing constraints (in loops) starting from 2 down to 1
                for spacing in [2, 1]:
                    if len(selected) >= num_transitions_needed:
                        break
                    for k, t_sec, rms in sorted_candidates:
                        if len(selected) >= num_transitions_needed:
                            break
                        # Check spacing against already selected loops
                        too_close = False
                        for sel_k, _, _ in selected:
                            if abs(k - sel_k) < spacing:
                                too_close = True
                                break
                        if not too_close:
                            selected.append((k, t_sec, rms))

                selected = sorted(selected, key=lambda x: x[0])
                aligned_loops = [x[0] for x in selected]

                report += f"Automatically detected top {len(selected)} pauses (spacing >= 1 loop):\n"
                for idx, (k, t_sec, rms) in enumerate(selected, 1):
                    report += f"  Transition {idx}: Loop {k} ({t_sec:.2f}s) | RMS: {rms:.5f}\n"

                # Show remaining candidates for debug
                report += "\nAll loop boundaries evaluated:\n"
                for k, t_sec, rms in sorted(candidates):
                    marker = "*" if k in aligned_loops else " "
                    report += (
                        f"  {marker} Loop {k:2d} ({t_sec:5.2f}s): RMS = {rms:.5f}\n"
                    )

            aligned_loops = sorted(list(set(aligned_loops)))
            loop_positions = ", ".join(str(l) for l in aligned_loops)

            # If no transitions found, default to 0 to prevent issues
            if not loop_positions:
                loop_positions = "0"

            return io.NodeOutput(loop_positions, report)

        except Exception as e:
            log.error(_LOG_PREFIX, f"Execution failed: {e}")
            return io.NodeOutput("0", f"Error during execution: {e}")
