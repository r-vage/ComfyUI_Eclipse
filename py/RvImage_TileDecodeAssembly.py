# Tile Decode & Assembly [Eclipse]
# Combines VAE tiled decode + list→batch conversion + tile assembly in a single node.
# Accepts latent tiles from sampler, decodes each with tiled VAE, assembles into final image.
# Pair with Tile Split [Eclipse] — connected via the tile_pipe output.

from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

import torch  # type: ignore
import numpy as np  # type: ignore
from PIL import Image

_LOG_PREFIX = "TileDecodeAssy"
_TILE_PIPE_TYPE = "eclipse_tile_pipe"


def _tensor2pil(tensor):
    return Image.fromarray(np.clip(255.0 * tensor.cpu().numpy(), 0, 255).astype(np.uint8))


def _pil2tensor(pil_image):
    return torch.from_numpy(np.array(pil_image).astype(np.float32) / 255.0)


def _create_gradient_mask(size, direction):
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
    blend_size = min(padding, overlap_size)

    if blend_size == 0:
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


def _assemble_tiles(tiles, positions, grid_size, padding):
    # tiles: [N, H, W, C] tensor batch
    num_cols, num_rows = grid_size

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

    return _pil2tensor(final).unsqueeze(0)


class RvImage_TileDecodeAssembly(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Tile Decode & Assembly [Eclipse]",
            display_name="Tile Decode & Assembly",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            inputs=[
                io.Latent.Input("samples", tooltip="Latent tile samples from sampler. "
                                "Accepts both batch and list — list is auto-converted to batch."),
                io.Vae.Input("vae", tooltip="VAE for tiled decoding."),
                io.Custom(_TILE_PIPE_TYPE).Input("tile_pipe",
                    tooltip="Tile pipe from Tile Split node."),
                io.Int.Input("tile_size", default=0, min=0, max=4096, step=32,
                             tooltip="VAE decode tile size in pixels. "
                                     "0 = auto from pipe (uses tile dimensions — optimal, no internal tiling). "
                                     "Set manually to reduce VRAM (e.g. 512 for SD/SDXL, 1024 for Flux2)."),
                io.Int.Input("overlap", default=0, min=0, max=4096, step=32,
                             tooltip="VAE decode tile overlap in pixels. "
                                     "0 = auto (tile_size // 8, min 32). "
                                     "Higher = less color banding, more VRAM. "
                                     "Only used when tile_size < tile dimensions."),
                io.Int.Input("padding", default=128, min=0, max=512, step=8,
                             tooltip="Image-level gradient blend width at tile seams."),
            ],
            outputs=[
                io.Image.Output("image", tooltip="Reconstructed full image."),
            ],
            is_input_list=True,
            description="All-in-one: VAE tiled decode + list→batch + tile assembly. "
                        "Eliminates VAE Decode (Tiled), IMAGE_LIST_TO_BATCH, and Tile Assembly nodes. "
                        "Connect tile_pipe from Tile Split.",
        )

    @classmethod
    def execute(cls, samples, vae, tile_pipe, tile_size, overlap, padding):
        # is_input_list=True: all inputs arrive as lists
        # Unwrap scalar inputs (vae, tile_pipe, tile_size, overlap, padding are single-element lists)
        vae = vae[0] if isinstance(vae, list) else vae
        tile_pipe = tile_pipe[0] if isinstance(tile_pipe, list) else tile_pipe
        tile_size = tile_size[0] if isinstance(tile_size, list) else tile_size
        overlap = overlap[0] if isinstance(overlap, list) else overlap
        padding = padding[0] if isinstance(padding, list) else padding

        # samples is a list of latent dicts — stack into single batch
        if isinstance(samples, list):
            latents = [s["samples"] for s in samples]
            latent = torch.cat(latents, dim=0)
        else:
            latent = samples["samples"]

        # Handle nested tensors
        if latent.is_nested:
            latent = torch.stack(latent.unbind())

        num_tiles = latent.shape[0]
        positions = tile_pipe["positions"]
        grid_size = tile_pipe["grid_size"]
        expected = grid_size[0] * grid_size[1]

        if num_tiles != expected:
            log.warning(_LOG_PREFIX, f"Tile count mismatch: got {num_tiles} latents, "
                        f"expected {expected} from grid {grid_size[0]}x{grid_size[1]}")

        # VAE tiled decode — mirrors ComfyUI's VAEDecodeTiled logic
        # Auto tile_size: use tile dimensions from pipe (decode each tile whole = no internal banding)
        if tile_size <= 0:
            pipe_tile_size = tile_pipe.get("tile_size")
            if pipe_tile_size:
                tile_size = max(pipe_tile_size[0], pipe_tile_size[1])
                log.msg(_LOG_PREFIX, f"Auto tile_size={tile_size} from pipe "
                        f"(tile dims {pipe_tile_size[0]}x{pipe_tile_size[1]})")
            else:
                tile_size = 1024
                log.warning(_LOG_PREFIX, "No tile_size in pipe — falling back to 1024")

        if overlap <= 0:
            overlap = max(32, tile_size // 8)

        if tile_size < overlap * 4:
            overlap = tile_size // 4

        temporal_compression = vae.temporal_compression_decode()
        if temporal_compression is not None:
            temporal_size = max(2, 64 // temporal_compression)
            temporal_overlap = max(1, min(temporal_size // 2, 8 // temporal_compression))
        else:
            temporal_size = None
            temporal_overlap = None

        compression = vae.spacial_compression_decode()

        log.msg(_LOG_PREFIX, f"Decoding {num_tiles} tiles — VAE tile_size={tile_size}, "
                f"overlap={overlap}, compression={compression}")

        images = vae.decode_tiled(
            latent,
            tile_x=tile_size // compression,
            tile_y=tile_size // compression,
            overlap=overlap // compression,
            tile_t=temporal_size,
            overlap_t=temporal_overlap,
        )

        if len(images.shape) == 5:
            images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])

        # Assemble tiles into final image
        result = _assemble_tiles(images, positions, grid_size, padding)
        return io.NodeOutput(result)
