import os
import random
from datetime import datetime
import folder_paths #type: ignore
from comfy_api.latest import io #type: ignore
from ...core import CATEGORY
from ...core.common import RESOLUTION_PRESETS, RESOLUTION_MAP, VIDEO_RESOLUTION_PRESETS, VIDEO_RESOLUTION_MAP

MAX_RESOLUTION = 32768

initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
eclipse_seed_random_state = random.getstate()
random.setstate(initial_random_state)


def new_random_seed():
    # Gets a new random seed from the eclipse_seed_random_state and resetting the previous state.
    global eclipse_seed_random_state
    prev_random_state = random.getstate()
    random.setstate(eclipse_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    eclipse_seed_random_state = random.getstate()
    random.setstate(prev_random_state)
    return seed

def format_datetime(datetime_format):
    today = datetime.now()
    try:
        timestamp = today.strftime(datetime_format)
    except Exception:
        timestamp = today.strftime("%Y-%m-%d-%H%M%S")
    return timestamp

def format_date_time(string, position, datetime_format):
    today = datetime.now()
    if position == "prefix":
        return f"{today.strftime(datetime_format)}_{string}"
    if position == "postfix":
        return f"{string}_{today.strftime(datetime_format)}"
    return string

def format_variables(string, input_variables):
    if input_variables is not None and str(input_variables).strip():
        variables = str(input_variables).split(",")
        return string.format(*variables)
    else:
        return string



class RvFolder_SmartFolder(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Smart Folder [Eclipse]",
            display_name="⚠ Smart Folder",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Combo.Input("generation_mode", options=["Image Mode", "Video Mode"], default="Image Mode", tooltip="Select generation mode: Image or Video"),
                io.String.Input("root_folder_image", default="images", tooltip="Root folder name for image generation."),
                io.String.Input("root_folder_video", default="videos", tooltip="Root folder name for video generation."),
                io.Boolean.Input("create_date_time_folder", default=True, label_on="yes", label_off="no", tooltip="Create date/time subfolder."),
                io.String.Input("date_time_format", default="%Y-%m-%d", tooltip="Date/time format for folder naming (strftime syntax)."),
                io.Combo.Input("date_time_position", options=["prefix", "postfix"], default="postfix", tooltip="Where to add date/time to folder name: prefix, or postfix."),
                io.Boolean.Input("create_batch_folder", default=False, label_on="yes", label_off="no", tooltip="Enable batch subfolder configuration."),
                io.String.Input("batch_folder_name", default="batch_{}", tooltip="Batch subfolder name. Supports variable formatting (e.g. batch_{})."),
                io.Int.Input("batch_number", default=1, min=1, max=0xffffffffffffffff, tooltip="Batch number to use in batch folder name."),
                io.Combo.Input("batch_number_control", options=["fixed", "increment"], default="fixed", tooltip="Control batch number behavior: fixed or increment after each queue."),
                # Image-specific parameters
                io.Boolean.Input("use_image_size", default=False, label_on="yes", label_off="no", tooltip="Enable image size configuration. Disable when using Smart Loader for latent size."),
                io.Combo.Input("image_size", options=RESOLUTION_PRESETS, tooltip="Image size preset."),
                io.Int.Input("width", default=512, min=16, max=MAX_RESOLUTION, step=8, tooltip="Image width in pixels."),
                io.Int.Input("height", default=512, min=16, max=MAX_RESOLUTION, step=8, tooltip="Image height in pixels."),
                # Video-specific parameters
                io.Combo.Input("video_size", options=VIDEO_RESOLUTION_PRESETS, tooltip="Video size preset."),
                io.Int.Input("video_width", default=576, min=16, max=MAX_RESOLUTION, step=1, tooltip="Video width in pixels."),
                io.Int.Input("video_height", default=1024, min=16, max=MAX_RESOLUTION, step=1, tooltip="Video height in pixels."),
                io.Float.Input("frame_rate", default=30.0, min=8.0, max=240.0, tooltip="Video frame rate (frames per second)."),
                io.Int.Input("frame_load_cap", default=162, min=0, max=MAX_RESOLUTION, step=1, tooltip="Maximum frames to load per batch. Set 0 for no limit."),
                io.Int.Input("context_length", default=81, min=1, max=MAX_RESOLUTION, step=1, tooltip="Context length for WAN models."),
                io.Int.Input("loop_count", default=0, min=0, max=MAX_RESOLUTION, step=1, tooltip="Calculates the frame_load_cap by using the context length * loop count. this overrides the frame_load_cap value if > 0."),
                io.Int.Input("overlap", default=0, min=0, max=MAX_RESOLUTION, step=1, tooltip="Overlap Frames between two clips."),
                io.Int.Input("skip_first_frames", default=0, min=0, max=4096, tooltip="Number of initial frames to skip."),
                io.Int.Input("skip_calculation", default=0, min=0, max=0xffffffffffffffff, tooltip="Additional skip first frames calculation: skip (context_length * value)."),
                io.Combo.Input("skip_calculation_control", options=["fixed", "increment"], default="fixed", tooltip="Control skip calculation behavior: fixed or increment after each queue."),
                io.Int.Input("select_every_nth", default=1, min=1, max=100, tooltip="Select every nth frame from input."),
                # Common parameters
                io.Int.Input("batch_size", default=1, min=1, max=4096, tooltip="Batch size (number of items per batch)."),
                io.Int.Input("seed", default=0, min=-3, max=2**64 - 1, tooltip="Random seed for folder naming. Use -1 for random, -2 to increment, -3 to decrement."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return new_random_seed()
        return seed

    @classmethod
    def execute(cls, generation_mode, root_folder_image, root_folder_video, create_date_time_folder, date_time_format, 
                date_time_position, create_batch_folder, batch_folder_name, batch_number, 
                batch_number_control, use_image_size, image_size, width, height, video_size, video_width, 
                video_height, frame_rate, frame_load_cap, context_length, loop_count, overlap, 
                skip_first_frames, skip_calculation, skip_calculation_control, 
                select_every_nth, batch_size, seed=0):

        # Type safety: ensure valid strings and numbers
        if not isinstance(root_folder_image, str) or not root_folder_image:
            root_folder_image = "images"
        if not isinstance(root_folder_video, str) or not root_folder_video:
            root_folder_video = "videos"
        if not isinstance(date_time_format, str) or not date_time_format:
            date_time_format = "%Y-%m-%d"
        if not isinstance(batch_folder_name, str) or not batch_folder_name:
            batch_folder_name = "batch_{}"

        # Validate numeric inputs
        if not isinstance(width, int) or width < 16:
            width = 512
        if not isinstance(height, int) or height < 16:
            height = 512
        if not isinstance(video_width, int) or video_width < 16:
            video_width = 576
        if not isinstance(video_height, int) or video_height < 16:
            video_height = 1024
        if not isinstance(frame_rate, (int, float)) or frame_rate < 8:
            frame_rate = 30.0
        if not isinstance(frame_load_cap, int):
            frame_load_cap = 162
        if not isinstance(context_length, int):
            context_length = 81
        if not isinstance(loop_count, int):
            loop_count = 0
        if not isinstance(overlap, int):
            overlap = 0
        if not isinstance(skip_first_frames, int):
            skip_first_frames = 0
        if not isinstance(skip_calculation, int):
            skip_calculation = 0
        if not isinstance(select_every_nth, int) or select_every_nth < 1:
            select_every_nth = 1
        if not isinstance(batch_size, int) or batch_size < 1:
            batch_size = 1
        if not isinstance(batch_number, int) or batch_number < 1:
            batch_number = 1
        
        # Ensure control values are valid
        if batch_number_control not in ["fixed", "increment"]:
            batch_number_control = "fixed"
        if skip_calculation_control not in ["fixed", "increment"]:
            skip_calculation_control = "fixed"

        # Format datetime
        mDate = format_datetime(date_time_format)
        root_folder = root_folder_image if generation_mode == "Image Mode" else root_folder_video
        new_path = root_folder

        # Add date/time prefix/postfix if configured
        if create_date_time_folder:
            if date_time_position == "prefix":
                new_path = os.path.join(mDate, root_folder)
            elif date_time_position == "postfix":
                new_path = os.path.join(root_folder, mDate)

        # Create batch folder if configured
        if create_batch_folder:
            folder_name_parsed = format_variables(batch_folder_name, batch_number)
            new_path = os.path.join(new_path, folder_name_parsed)

        # Build output path
        path_out = os.path.join(folder_paths.get_output_directory(), new_path)

        # Create pipe based on generation mode
        if generation_mode == "Image Mode":
            # Build Image pipe (do NOT include video keys)
            pipe = {
                "path": path_out,
                "batch_size": batch_size,
                "seed": int(seed),  # Include seed in pipe for downstream use
            }

            # Only include image size if use_image_size is enabled
            if use_image_size:
                if image_size in RESOLUTION_MAP:
                    width, height = RESOLUTION_MAP[image_size]
                pipe["width"] = width
                pipe["height"] = height

        else:  # Video
            # Handle video resolution preset
            if video_size in VIDEO_RESOLUTION_MAP:
                video_width, video_height = VIDEO_RESOLUTION_MAP[video_size]

            # Handle loop_count override for frame_load_cap
            if loop_count > 0:
                frame_load_cap = context_length * loop_count

            # Handle skip_calculation
            if skip_calculation > 0:
                try:
                    skip_first_frames += (context_length * skip_calculation)
                except Exception:
                    skip_first_frames = 0

            # Build Video pipe
            pipe = {
                "path": path_out,
                "width": video_width,
                "height": video_height,
                "frame_rate": float(frame_rate),
                "frame_load_cap": int(frame_load_cap),
                "context_length": int(context_length),
                "overlap": int(overlap),
                "skip_first_frames": int(skip_first_frames),
                "select_every_nth": int(select_every_nth),
                "batch_size": int(batch_size),
                "seed": int(seed),  # Include seed in pipe for downstream use
            }

        return io.NodeOutput(pipe)