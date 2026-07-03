# Tile Assembly [Eclipse]
# Reassembles processed tiles into a single image with smooth gradient blending.
# Pair with Tile Split [Eclipse] — connected via the tile_pipe output.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY

import torch #type: ignore
import numpy as np #type: ignore
from PIL import Image

_TILE_PIPE_TYPE = "eclipse_tile_pipe"


def _tensor2pil(tensor):
    # Expects [H, W, C] float tensor
    return Image.fromarray(np.clip(255.0 * tensor.cpu().numpy(), 0, 255).astype(np.uint8))


def _pil2tensor(pil_image):
    # Returns [H, W, C] float tensor
    return torch.from_numpy(np.array(pil_image).astype(np.float32) / 255.0)


def _create_gradient_mask(size, direction):
    # size = (width, height) for PIL
    mask = Image.new("L", size)
    if direction == "horizontal":
        for i in range(size[0]):
            value = int(255 * (1 - i / size[0]))
            mask.paste(value, (i, 0, i + 1, size[1]))
    else:
        for i in range(size[1]):
            value = int(255 * (1 - i / size[1]))
            mask.paste(value, (0, i, size[0], i + 1))
    return mask


def _blend_tiles(tile1, tile2, overlap_size, direction, padding):
    # Blend two PIL images along their overlap region
    blend_size = min(padding, overlap_size)

    if blend_size == 0:
        # No blending — hard cut at overlap boundary
        if direction == "horizontal":
            result = Image.new("RGB", (tile1.width + tile2.width - overlap_size, tile1.height))
            result.paste(tile1.crop((0, 0, tile1.width - overlap_size, tile1.height)), (0, 0))
            result.paste(tile2, (tile1.width - overlap_size, 0))
        else:
            result = Image.new("RGB", (tile1.width, tile1.height + tile2.height - overlap_size))
            result.paste(tile1.crop((0, 0, tile1.width, tile1.height - overlap_size)), (0, 0))
            result.paste(tile2, (0, tile1.height - overlap_size))
        return result

    offset_total = overlap_size - blend_size

    if direction == "horizontal":
        offset_left = offset_total // 2
        offset_right = offset_total - offset_left

        mask = _create_gradient_mask((blend_size, tile1.height), "horizontal")
        crop1 = tile1.crop((tile1.width - overlap_size + offset_left, 0,
                            tile1.width - offset_right, tile1.height))
        crop2 = tile2.crop((offset_left, 0, offset_left + blend_size, tile2.height))

        if crop1.size != crop2.size:
            raise ValueError(f"Blend crop mismatch: {crop1.size} vs {crop2.size}")

        blended = Image.composite(crop1, crop2, mask)
        result = Image.new("RGB", (tile1.width + tile2.width - overlap_size, tile1.height))
        result.paste(tile1.crop((0, 0, tile1.width - overlap_size + offset_left, tile1.height)), (0, 0))
        result.paste(blended, (tile1.width - overlap_size + offset_left, 0))
        result.paste(tile2.crop((offset_left + blend_size, 0, tile2.width, tile2.height)),
                     (tile1.width - offset_right, 0))
    else:
        offset_top = offset_total // 2
        offset_bottom = offset_total - offset_top

        mask = _create_gradient_mask((tile1.width, blend_size), "vertical")
        crop1 = tile1.crop((0, tile1.height - overlap_size + offset_top,
                            tile1.width, tile1.height - offset_bottom))
        crop2 = tile2.crop((0, offset_top, tile2.width, offset_top + blend_size))

        if crop1.size != crop2.size:
            raise ValueError(f"Blend crop mismatch: {crop1.size} vs {crop2.size}")

        blended = Image.composite(crop1, crop2, mask)
        result = Image.new("RGB", (tile1.width, tile1.height + tile2.height - overlap_size))
        result.paste(tile1.crop((0, 0, tile1.width, tile1.height - overlap_size + offset_top)), (0, 0))
        result.paste(blended, (0, tile1.height - overlap_size + offset_top))
        result.paste(tile2.crop((0, offset_top + blend_size, tile2.width, tile2.height)),
                     (0, tile1.height - offset_bottom))

    return result


class RvImage_TileAssembly(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Tile Assembly [Eclipse]",
            display_name="Tile Assembly",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Image.Input("tiles", tooltip="Batch of processed tile images."),
                io.Custom(_TILE_PIPE_TYPE).Input("tile_pipe",
                    tooltip="Tile pipe from Tile Split node (positions, original size, grid size)."),
                io.Int.Input("padding", default=64, min=0, max=512, step=8,
                             tooltip="Gradient blend width in pixels at tile overlaps."),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Reconstructed full image."),
            ],
            description="Reassemble tiles into a single image with smooth gradient blending. "
                        "Connect tile_pipe from Tile Split to carry positions and grid metadata.",
        )

    @classmethod
    def execute(cls, tiles, tile_pipe, padding):
        positions = tile_pipe["positions"]
        grid_size = tile_pipe["grid_size"]
        num_cols, num_rows = grid_size

        # Blend each row horizontally
        row_images = []
        for row in range(num_rows):
            row_img = _tensor2pil(tiles[row * num_cols])
            for col in range(1, num_cols):
                idx = row * num_cols + col
                tile_img = _tensor2pil(tiles[idx])
                prev_right = positions[idx - 1][2]
                left = positions[idx][0]
                overlap_w = prev_right - left
                if overlap_w > 0:
                    row_img = _blend_tiles(row_img, tile_img, overlap_w, "horizontal", padding)
                else:
                    combined = Image.new("RGB",
                                         (row_img.width + tile_img.width,
                                          max(row_img.height, tile_img.height)))
                    combined.paste(row_img, (0, 0))
                    combined.paste(tile_img, (row_img.width, 0))
                    row_img = combined
            row_images.append(row_img)

        # Blend rows vertically
        final = row_images[0]
        for row in range(1, num_rows):
            prev_lower = positions[(row - 1) * num_cols][3]
            upper = positions[row * num_cols][1]
            overlap_h = prev_lower - upper
            if overlap_h > 0:
                final = _blend_tiles(final, row_images[row], overlap_h, "vertical", padding)
            else:
                combined = Image.new("RGB",
                                     (max(final.width, row_images[row].width),
                                      final.height + row_images[row].height))
                combined.paste(final, (0, 0))
                combined.paste(row_images[row], (0, final.height))
                final = combined

        result = _pil2tensor(final).unsqueeze(0)
        return io.NodeOutput(result)
