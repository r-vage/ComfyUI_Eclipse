import os
import cv2 # type: ignore
import numpy as np # type: ignore
import torch # type: ignore
from typing import Optional
from ...core import CATEGORY
from ...core.logger import log
from comfy_api.latest import io #type: ignore

_LOG_PREFIX = "Video Combine"
FPS = 30.0


def _load_video_frames(video_path: str, max_frames: Optional[int] = None) -> list[np.ndarray]:
    if not os.path.exists(video_path):
        raise ValueError(f"Video file not found: {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"Could not open video file: {video_path}")
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    log.msg(_LOG_PREFIX, f"Video {video_path}: {total_frames} frames, {fps} fps")
    frames = []
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frames.append(frame_rgb)
        frame_count += 1
        if max_frames and frame_count >= max_frames:
            break
    cap.release()
    if not frames:
        raise ValueError(f"No frames could be loaded from video: {video_path}")
    log.msg(_LOG_PREFIX, f"Successfully loaded {len(frames)} frames from {video_path}")
    return frames


def _frames_to_tensor(frames_list: list[np.ndarray]) -> torch.Tensor:
    if not frames_list:
        raise ValueError("Empty frames list provided")
    tensor_frames = [(frame.astype(np.float32) / 255.0) for frame in frames_list]
    tensor_output = torch.from_numpy(np.stack(tensor_frames, axis=0))
    return tensor_output


class RvTools_VideoClips_Combine(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Combine Video Clips [Eclipse]",
            display_name="⚠ Combine Video Clips",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with 'Load Batch From Folder'. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Int.Input("frame_load_cap", default=81, min=1, max=10000, step=1, tooltip="Total number of frames to load from each video."),
                io.Boolean.Input("simple_combine", default=False, tooltip="If True, combines only the video files (ignores join files)."),
                io.String.Input("video_filelist", default="", multiline=False, optional=True, tooltip="Comma-separated list of video file paths."),
                io.String.Input("joined_filelist", default="", multiline=False, optional=True, tooltip="Comma-separated list of join file paths."),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Float.Output("fps"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Re-execute when video files change on disk.
        video_filelist = kwargs.get("video_filelist", "")
        joined_filelist = kwargs.get("joined_filelist", "")
        mtimes = []
        for filelist in (video_filelist, joined_filelist):
            if not filelist or filelist in ('', 'undefined', 'none'):
                continue
            for v in str(filelist).splitlines():
                v = v.strip().strip('"\'')
                if not v:
                    continue
                if os.path.exists(v):
                    mtimes.append(os.path.getmtime(v))
        return str(mtimes)

    @classmethod
    def execute(cls, frame_load_cap, simple_combine, video_filelist=None, joined_filelist=None) -> io.NodeOutput:
        videos = None
        joined = None
        if video_filelist not in (None, '', 'undefined', 'none'):
            videos = [line.strip().strip('"\'') for line in str(video_filelist).splitlines() if line.strip()]
        if joined_filelist not in (None, '', 'undefined', 'none'):
            joined = [line.strip().strip('"\'') for line in str(joined_filelist).splitlines() if line.strip()]

        output_images_list: list[np.ndarray] = []
        video_1_start_idx = 0
        video_1_end_idx = 0

        if videos and not simple_combine:
            last_was_join = False
            for i in range(len(videos)):
                video_1_list = []
                video_join_list = []
                video_1_exists = False
                join_exists = False
                video_1 = str(videos[i]).strip()
                video_1_exists = os.path.exists(video_1)
                if last_was_join:
                    video_join = str(joined[i]).strip() if (joined and i < len(joined)) else ""
                    join_exists = bool(video_join and os.path.exists(video_join))
                    if join_exists:
                        video_join_list.extend(_load_video_frames(video_join))
                        if video_join_list:
                            output_images_list.extend(video_join_list)
                    else:
                        last_was_join = False
                        if video_1_exists:
                            video_1_list = _load_video_frames(video_1, frame_load_cap)
                        if video_1_list:
                            video_1_start_idx = frame_load_cap // 2
                            video_1_start_idx = min(video_1_start_idx, len(video_1_list))
                            video_1_end_idx = frame_load_cap
                            video_1_end_idx = min(video_1_end_idx, len(video_1_list))
                            log.msg(_LOG_PREFIX, f"Adding Frames video_1 [{video_1_start_idx}:{video_1_end_idx}]")
                            for idx in range(video_1_start_idx, video_1_end_idx):
                                if idx < len(video_1_list):
                                    output_images_list.append(video_1_list[idx])
                else:
                    if video_1_exists:
                        video_1_list = _load_video_frames(video_1, frame_load_cap)
                    video_join = str(joined[i]).strip() if (joined and i < len(joined)) else ""
                    join_exists = bool(video_join and os.path.exists(video_join))
                    if join_exists:
                        video_join_list.extend(_load_video_frames(video_join))
                        if video_1_list:
                            video_1_start_idx = 0
                            video_1_end_idx = frame_load_cap // 2
                            video_1_end_idx = min(video_1_end_idx, len(video_1_list))
                            log.msg(_LOG_PREFIX, f"Adding Frames video_1 [{video_1_start_idx}:{video_1_end_idx}]")
                            for idx in range(video_1_start_idx, video_1_end_idx):
                                if idx < len(video_1_list):
                                    output_images_list.append(video_1_list[idx])
                        if video_join_list:
                            log.msg(_LOG_PREFIX, f"Adding Frames video_join: {len(video_join_list)}")
                            output_images_list.extend(video_join_list)
                            last_was_join = True
                    else:
                        if video_1_list:
                            video_1_start_idx = 0
                            video_1_end_idx = frame_load_cap
                            video_1_end_idx = min(video_1_end_idx, len(video_1_list))
                            log.msg(_LOG_PREFIX, f"Adding Frames video_1 [{video_1_start_idx}:{video_1_end_idx}]")
                            for idx in range(video_1_start_idx, video_1_end_idx):
                                if idx < len(video_1_list):
                                    output_images_list.append(video_1_list[idx])
        elif videos and simple_combine:
            for i in range(len(videos)):
                video = str(videos[i]).strip()
                if os.path.exists(video):
                    try:
                        output_images_list.extend(_load_video_frames(video))
                    except Exception as e:
                        log.error(_LOG_PREFIX, f"Error loading video frames: {str(e)}")
                        raise ValueError(f"Error loading video frames: {str(e)}")
        if not output_images_list:
            raise ValueError("No output images generated")
        log.msg(_LOG_PREFIX, f"Generated {len(output_images_list)} total output images")
        try:
            image_tensor = _frames_to_tensor(output_images_list)
            log.msg(_LOG_PREFIX, f"Image tensor shape: {image_tensor.shape}")
            log.msg(_LOG_PREFIX, f"Video combination completed successfully")
            return io.NodeOutput(image_tensor, FPS)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error creating tensor: {str(e)}")
            raise ValueError(f"Error creating output tensor: {str(e)}")
