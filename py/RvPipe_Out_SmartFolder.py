import torch #type: ignore
import comfy.model_management #type: ignore
import folder_paths #type: ignore
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

def _generate_latent(width, height, batch_size=1, channels=4, downscale=8):
    # Generate empty latent tensor for image generation
    device = comfy.model_management.intermediate_device()
    latent = torch.zeros([batch_size, channels, height // downscale, width // downscale], device=device)
    return {"samples": latent, "downscale_ratio_spacial": downscale}

class RvPipe_Out_SmartFolder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out Smart Folder [Eclipse]",
            display_name="Pipe Out Smart Folder",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input pipe from Smart Folder containing generation mode (image/video) and all relevant parameters."),
            ],
            outputs=[
                io.String.Output("path"),
                io.Int.Output("width"),
                io.Int.Output("height"),
                io.Int.Output("batch_size"),
                io.Latent.Output("latent"),
                io.Float.Output("frame_rate"),
                io.Int.Output("frame_load_cap"),
                io.Int.Output("context_length"),
                io.Int.Output("overlap"),
                io.Int.Output("skip_first_frames"),
                io.Int.Output("select_every_nth"),
                io.Int.Output("seed"),
                io.Int.Output("loop_count"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_SmartFolder expects dict-style pipes only.")

        path = pipe.get("path") or ""
        if not path:
            path = folder_paths.get_output_directory()
        
        width = pipe.get("width")
        height = pipe.get("height")
        batch_size = pipe.get("batch_size")
        latent_channels = pipe.get("latent_channels", 4)
        latent_downscale = pipe.get("latent_downscale", 8)

        output_latent = None
        if width is not None and height is not None and batch_size is not None:
            try:
                output_latent = _generate_latent(int(width), int(height), int(batch_size), int(latent_channels), int(latent_downscale))
            except Exception:
                output_latent = None

        frame_rate = pipe.get("frame_rate")
        frame_load_cap = pipe.get("frame_load_cap")
        context_length = pipe.get("context_length")
        overlap = pipe.get("overlap")
        skip_first_frames = pipe.get("skip_first_frames")
        select_every_nth = pipe.get("select_every_nth")
        
        try:
            seed_val = pipe.get("seed")
            seed = int(seed_val) if seed_val is not None else None
        except Exception:
            seed = None

        try:
            lc_val = pipe.get("loop_count")
            loop_count = int(lc_val) if lc_val is not None else None
        except Exception:
            loop_count = None

        return io.NodeOutput(
            path, width, height, batch_size, output_latent,
            frame_rate, frame_load_cap, context_length, overlap,
            skip_first_frames, select_every_nth, seed, loop_count,
        )
