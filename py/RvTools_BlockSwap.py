# Universal Block Swap V3 adapter. Shared behavior lives in neutral core infrastructure.

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.blockswap import (
    _count_blocks,
    _detect_block_groups,
    _detect_offloadable,
    _get_model_arch_name,
    _is_native_dynamic_vram,
    _iter_blocks,
    _make_swap_callback,
    _offload_module,
    apply_blockswap,
)


class RvTools_BlockSwap(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Universal Block Swap [Eclipse]",
            display_name="Universal Block Swap",
            description=(
                "Offloads transformer blocks from GPU to CPU to reduce VRAM usage. "
                "Uses ComfyUI's native weight-casting system and supports common "
                "DiT architectures. Native dynamic VRAM is detected automatically."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.Model.Input(
                    "model", tooltip="The diffusion model to apply block swapping to."
                ),
                io.Int.Input(
                    "blocks_to_swap",
                    default=10,
                    min=0,
                    max=100,
                    step=1,
                    tooltip=(
                        "Number of transformer blocks to offload from GPU to CPU. "
                        "Higher values save VRAM but reduce inference speed. Set to 0 to disable."
                    ),
                ),
                io.Boolean.Input(
                    "offload_embeddings",
                    default=False,
                    label_on="Yes",
                    label_off="No",
                    tooltip="Also offload compatible embedding and projection layers.",
                ),
            ],
            outputs=[io.Model.Output("model")],
        )

    @classmethod
    def execute(cls, model, blocks_to_swap, offload_embeddings):
        patched = apply_blockswap(
            model,
            blocks_to_swap,
            offload_embeddings,
            "BlockSwap",
        )
        return io.NodeOutput(patched)


__all__ = [
    "RvTools_BlockSwap",
    "_count_blocks",
    "_detect_block_groups",
    "_detect_offloadable",
    "_get_model_arch_name",
    "_is_native_dynamic_vram",
    "_iter_blocks",
    "_make_swap_callback",
    "_offload_module",
]
