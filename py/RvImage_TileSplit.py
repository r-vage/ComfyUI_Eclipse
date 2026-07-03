# Tile Split [Eclipse]
# Splits an image into overlapping tiles for batch processing.
# VAE-aware: detects spatial compression ratio to ensure correct tile alignment.
# Replaces TTP_Tile_imageSize + TTP_Image_Tile_Batch in a single node.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

import torch #type: ignore
import numpy as np #type: ignore
from PIL import Image

_LOG_PREFIX = "TileSplit"
_TILE_PIPE_TYPE = "eclipse_tile_pipe"


def _tensor2pil(tensor):
    # Expects [H, W, C] float tensor
    return Image.fromarray(np.clip(255.0 * tensor.cpu().numpy(), 0, 255).astype(np.uint8))


def _pil2tensor(pil_image):
    # Returns [H, W, C] float tensor
    return torch.from_numpy(np.array(pil_image).astype(np.float32) / 255.0)


def _align_up(value, multiple):
    return ((value + multiple - 1) // multiple) * multiple


def _align_down(value, multiple):
    return (value // multiple) * multiple


class RvImage_TileSplit(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Tile Split [Eclipse]",
            display_name="Tile Split",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("image", tooltip="Image to split into tiles."),
                io.Vae.Input("vae", optional=True, tooltip="VAE for spatial ratio detection. "
                             "Auto-aligns tiles to the correct multiple (8 for SD/SDXL/Flux1, 16 for Flux2). "
                             "Highly recommended — prevents tile size mismatch during VAE encode."),
                io.Int.Input("width_factor", default=2, min=1, max=10, step=1,
                             tooltip="Number of tile columns."),
                io.Int.Input("height_factor", default=3, min=1, max=10, step=1,
                             tooltip="Number of tile rows."),
                io.Float.Input("overlap_rate", default=0.15, min=0.0, max=0.95, step=0.05,
                               tooltip="Overlap between adjacent tiles. 0 = no overlap."),
                io.Int.Input("align_override", default=0, min=0, max=64, step=8,
                             tooltip="Manual alignment override (pixels). 0 = auto from VAE. "
                                     "Use 16 for Flux2 if VAE is not connected."),
            ],
            outputs=[
                io.Image.Output("images", tooltip="List of tile images (one per tile).",
                                is_output_list=True),
                io.Custom(_TILE_PIPE_TYPE).Output("tile_pipe",
                    tooltip="Tile pipe — positions, original size, grid size. Connect to Tile Assembly."),
            ],
            description="Split an image into overlapping tiles for list-based processing. "
                        "Outputs a list so each tile can be processed independently by samplers. "
                        "VAE-aware alignment ensures correct dimensions for any model (SD/SDXL/Flux1/Flux2).",
        )

    @classmethod
    def execute(cls, image, width_factor, height_factor, overlap_rate,
                vae=None, align_override=0):
        # Determine alignment multiple
        align_to = 8
        source = "default"

        if align_override > 0:
            align_to = align_override
            source = "override"
        elif vae is not None:
            try:
                ratio = vae.spacial_compression_encode()
                if isinstance(ratio, (int, float)) and ratio > 0:
                    align_to = int(ratio)
                    source = "VAE"
            except Exception:
                pass

        _, raw_H, raw_W, _ = image.shape

        # Calculate tile dimensions
        # No overlap: round UP (tiles must cover the area)
        # With overlap: round DOWN (overlap compensates)
        if overlap_rate == 0:
            tile_w = raw_W if width_factor == 1 else _align_up(raw_W // width_factor, align_to)
            tile_h = raw_H if height_factor == 1 else _align_up(raw_H // height_factor, align_to)
        else:
            tile_w = raw_W if width_factor == 1 else _align_down(
                int(raw_W / (1 + (width_factor - 1) * (1 - overlap_rate))), align_to)
            tile_h = raw_H if height_factor == 1 else _align_down(
                int(raw_H / (1 + (height_factor - 1) * (1 - overlap_rate))), align_to)

        # Safety: ensure tile dimensions are at least align_to
        tile_w = max(tile_w, align_to)
        tile_h = max(tile_h, align_to)

        log.msg(_LOG_PREFIX, f"{raw_W}x{raw_H} → tiles {tile_w}x{tile_h} "
                f"(grid={width_factor}x{height_factor}, overlap={overlap_rate}, "
                f"align={align_to} [{source}])")

        # Split image into tiles
        pil_img = _tensor2pil(image[0])
        img_w, img_h = pil_img.size

        # Single-tile fast path
        if img_w <= tile_w and img_h <= tile_h:
            tile_pipe = {
                "positions": [(0, 0, img_w, img_h)],
                "original_size": (img_w, img_h),
                "grid_size": (1, 1),
                "tile_size": (img_w, img_h),
            }
            return io.NodeOutput([image], tile_pipe)

        # Calculate steps with overlap
        def calc_step(size, tile_size):
            if size <= tile_size:
                return 1, 0
            num_tiles = (size + tile_size - 1) // tile_size
            overlap = (num_tiles * tile_size - size) // (num_tiles - 1)
            step = tile_size - overlap
            return num_tiles, step

        num_cols, step_x = calc_step(img_w, tile_w)
        num_rows, step_y = calc_step(img_h, tile_h)

        tiles = []
        positions = []
        for row in range(num_rows):
            for col in range(num_cols):
                left = col * step_x
                upper = row * step_y
                right = min(left + tile_w, img_w)
                lower = min(upper + tile_h, img_h)

                # Snap edge tiles to full tile size
                if right - left < tile_w:
                    left = max(0, img_w - tile_w)
                if lower - upper < tile_h:
                    upper = max(0, img_h - tile_h)

                tile = pil_img.crop((left, upper, right, lower))
                tiles.append(_pil2tensor(tile))
                positions.append((left, upper, right, lower))

        tiles_list = [t.unsqueeze(0) for t in tiles]

        tile_pipe = {
            "positions": positions,
            "original_size": (img_w, img_h),
            "grid_size": (num_cols, num_rows),
            "tile_size": (tile_w, tile_h),
        }

        return io.NodeOutput(tiles_list, tile_pipe)
