import os
import cv2 #type: ignore
import numpy as np # type: ignore
import torch #type: ignore
 
from typing import Optional
from ...core import CATEGORY
from ...core.logger import log
from comfy_api.latest import io #type: ignore
 
_LOG_PREFIX = "WanVideo"
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
 
 
def _create_solid_color_image(reference_frame: np.ndarray, color_hex: str) -> np.ndarray:
    height, width = reference_frame.shape[:2]
    color_hex = color_hex.lstrip('#')
    r, g, b = tuple(int(color_hex[i:i+2], 16) for i in (0, 2, 4))
    solid_image = np.full((height, width, 3), [r, g, b], dtype=np.uint8)
    return solid_image
 
 
def _frames_to_tensor(frames_list: list[np.ndarray]) -> torch.Tensor:
    if not frames_list:
        raise ValueError("Empty frames list provided")
    tensor_frames = [(frame.astype(np.float32) / 255.0) for frame in frames_list]
    tensor_output = torch.from_numpy(np.stack(tensor_frames, axis=0))
    return tensor_output
 
 
class RvTools_VideoClips_SeamlessJoin(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Seamless Join Video Clips [Eclipse]",
            display_name="⚠ Seamless Join Video Clips",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — This node is deprecated and will be removed in v4.0.0.",
            inputs=[
                io.Int.Input("frame_load_cap", default=81, min=1, max=10000, step=1, tooltip="Total number of frames to load from each video."),
                io.Int.Input("mask_first_frames", default=10, min=0, max=1000, step=1, tooltip="Number of mask frames to add at the start of the transition."),
                io.Int.Input("mask_last_frames", default=0, min=0, max=1000, step=1, tooltip="Number of mask frames to add at the end of the transition."),
                io.String.Input("video_filelist", default="", multiline=False, optional=True, tooltip="Comma-separated list of video file paths."),
            ],
            outputs=[
                io.Image.Output("image"),
                io.Image.Output("mask"),
            ],
        )
 
    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Re-execute when video files change on disk.
        video_filelist = kwargs.get("video_filelist", "")
        if not video_filelist or video_filelist in ('', 'undefined', 'none'):
            return ""
        videos = str(video_filelist).split(', ')
        mtimes = []
        for v in videos:
            v = v.strip()
            if os.path.exists(v):
                mtimes.append(os.path.getmtime(v))
        return str(mtimes)
 
    @classmethod
    def execute(cls, frame_load_cap, mask_first_frames, mask_last_frames, video_filelist=None) -> io.NodeOutput:
        videos = None
        if video_filelist not in (None, '', 'undefined', 'none'):
            videos = str(video_filelist).split(', ')
        log.msg(_LOG_PREFIX, f"Starting process with parameters:")
        log.msg(_LOG_PREFIX, f"mask_last_frames: {mask_last_frames}")
        log.msg(_LOG_PREFIX, f"mask_first_frames: {mask_first_frames}")
        log.msg(_LOG_PREFIX, f"frame_load_cap: {frame_load_cap}")
        if not videos:
            raise ValueError("No valid video files provided. Please specify video_filelist with comma-separated video paths.")
 
        video_first = str(videos[0]).strip()
        video_second = str(videos[-1]).strip()
        log.msg(_LOG_PREFIX, f"video_first: {video_first}")
        log.msg(_LOG_PREFIX, f"video_second: {video_second}")
        if not os.path.exists(video_first):
            raise ValueError(f"First video file not found: {video_first}")
        if not os.path.exists(video_second):
            raise ValueError(f"Last video file not found: {video_second}")
        log.msg(_LOG_PREFIX, f"Both video files found, loading frames...")
        try:
            first_images_list = _load_video_frames(video_first, frame_load_cap * 2)
            second_images_list = _load_video_frames(video_second, frame_load_cap * 2)
            log.msg(_LOG_PREFIX, f"Loaded {len(first_images_list)} frames from first video")
            log.msg(_LOG_PREFIX, f"Loaded {len(second_images_list)} frames from second video")
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error loading video frames: {str(e)}")
            raise ValueError(f"Error loading video frames: {str(e)}")
        if not first_images_list or not second_images_list:
            raise ValueError("Could not load frames from one or both videos")
        reference_frame = first_images_list[0]
        output_images_list: list[np.ndarray] = []
        first_images_start_index = frame_load_cap // 2
        first_images_end_index = frame_load_cap - mask_last_frames
        first_images_start_index = max(0, min(first_images_start_index, len(first_images_list)))
        first_images_end_index = max(first_images_start_index, min(first_images_end_index, len(first_images_list)))
        for idx in range(first_images_start_index, first_images_end_index):
            if idx < len(first_images_list):
                output_images_list.append(first_images_list[idx])
        total_mask_count = mask_last_frames + mask_first_frames
        grey_image = _create_solid_color_image(reference_frame, "#7F7F7F")
        for _ in range(total_mask_count):
            output_images_list.append(grey_image.copy())
        second_images_start_index = mask_first_frames
        second_images_end_index = frame_load_cap // 2
        second_images_start_index = max(0, min(second_images_start_index, len(second_images_list)))
        second_images_end_index = max(second_images_start_index, min(second_images_end_index, len(second_images_list)))
        for idx in range(second_images_start_index, second_images_end_index):
            if idx < len(second_images_list):
                output_images_list.append(second_images_list[idx])
        output_mask_list: list[np.ndarray] = []
        first_mask_start_index = frame_load_cap // 2
        first_mask_end_index = frame_load_cap - mask_last_frames
        black_image = _create_solid_color_image(reference_frame, "#000000")
        white_image = _create_solid_color_image(reference_frame, "#FFFFFF")
        first_mask_count = first_mask_end_index - first_mask_start_index
        first_mask_count = max(0, first_mask_count)
        for _ in range(first_mask_count):
            output_mask_list.append(black_image.copy())
        for _ in range(total_mask_count):
            output_mask_list.append(white_image.copy())
        second_mask_start_index = mask_first_frames
        second_mask_end_index = frame_load_cap // 2
        second_mask_count = second_mask_end_index - second_mask_start_index
        second_mask_count = max(0, second_mask_count)
        for _ in range(second_mask_count):
            output_mask_list.append(black_image.copy())
        if not output_images_list:
            raise ValueError("No output images generated")
        if not output_mask_list:
            raise ValueError("No output masks generated")
        log.msg(_LOG_PREFIX, f"Generated {len(output_images_list)} output images")
        log.msg(_LOG_PREFIX, f"Generated {len(output_mask_list)} output masks")
        try:
            image_tensor = _frames_to_tensor(output_images_list)
            mask_tensor = _frames_to_tensor(output_mask_list)
            log.msg(_LOG_PREFIX, f"Image tensor shape: {image_tensor.shape}")
            log.msg(_LOG_PREFIX, f"Mask tensor shape: {mask_tensor.shape}")
            log.msg(_LOG_PREFIX, f"Processing completed successfully")
            return io.NodeOutput(image_tensor, mask_tensor)
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error creating tensors: {str(e)}")
            raise ValueError(f"Error creating output tensors: {str(e)}")
