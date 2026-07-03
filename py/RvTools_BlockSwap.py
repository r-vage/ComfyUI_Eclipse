#
# Universal Block Swap for DiT diffusion models
# Offloads transformer blocks to CPU to reduce VRAM usage.
# Uses ComfyUI's ON_LOAD callback — non-invasive, works alongside LoRA and hooks.
#
# Instead of dynamically moving blocks between GPU and CPU during forward
# (which causes CUDA errors from conflicting async stream operations),
# this activates ComfyUI's native comfy_cast_weights system on offloaded
# blocks.  Each Linear/Conv/Norm operation temporarily casts its weight to
# GPU for the computation then releases it — the same pipeline ComfyUI uses
# for its own lowvram offloading.
#
# Supported architectures (auto-detected):
#   WAN 2.1, Flux, Chroma, ChromaRadiance, SD3, LTXV,
#   HunyuanVideo, Cosmos, ZImage/NextDiT, QwenImage
#
# Based on research from ComfyUI-wanBlockswap and ComfyUI-MultiGPU.

import gc

import torch #type: ignore
from torch import nn #type: ignore
from comfy_api.latest import io #type: ignore
from comfy.patcher_extension import CallbacksMP #type: ignore
import comfy.model_management #type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "BlockSwap"


# ── Native dynamic VRAM detection ─────────────────────────────────────

def _is_native_dynamic_vram(model_patcher) -> bool:
    # ComfyUI 0.18.0+ has improved dynamic VRAM management that handles
    # weight offloading natively, making BlockSwap redundant.
    # Detection: backup_buffers was added to ModelPatcher in 0.18.0.
    # Combined with is_dynamic() to only skip when dynamic VRAM is active.
    return model_patcher.is_dynamic() and hasattr(model_patcher, 'backup_buffers')


# ── Architecture detection ────────────────────────────────────────────

# Ordered list of known block attribute names on diffusion_model.
# Each entry: (attr_name, description)
_KNOWN_BLOCK_ATTRS = [
    ("double_blocks", "double"),
    ("single_blocks", "single"),
    ("double_stream_blocks", "double_stream"),
    ("single_stream_blocks", "single_stream"),
    ("blocks",              "blocks"),
    ("transformer_blocks",  "transformer"),
    ("layers",              "layers"),
    ("joint_blocks",        "joint"),
]

# Components that can optionally be offloaded to save additional VRAM.
_KNOWN_OFFLOADABLE = [
    "text_embedding", "img_emb",
    "img_in", "txt_in", "time_in", "vector_in", "guidance_in",
    "x_embedder", "cap_embedder", "t_embedder", "y_embedder",
    "time_text_embed", "txt_norm",
    "noise_refiner", "context_refiner", "context_embedder",
    "patch_embedding", "time_embedding",
    "head", "final_layer", "norm_out", "proj_out",
]


def _detect_block_groups(diffusion_model) -> list[tuple[str, nn.Module]]:
    # Find all nn.ModuleList / nn.ModuleDict block containers on the diffusion model.
    # Returns list of (attr_name, module_container) tuples.
    groups: list[tuple[str, nn.Module]] = []
    for attr, _desc in _KNOWN_BLOCK_ATTRS:
        container = getattr(diffusion_model, attr, None)
        if container is None:
            continue
        if isinstance(container, (nn.ModuleList, nn.ModuleDict)) and len(container) > 0:
            groups.append((attr, container))
    return groups


def _count_blocks(groups: list[tuple[str, nn.Module]]) -> int:
    return sum(len(container) for _, container in groups)


def _iter_blocks(container: nn.Module):
    # Yield (key_part, block) tuples from ModuleList or ModuleDict.
    # key_part is the path fragment needed for model_patcher key construction
    # (e.g. "5" for ModuleList index, "block5" for ModuleDict key).
    if isinstance(container, nn.ModuleDict):
        # Cosmos uses ModuleDict with keys "block0", "block1", …
        for key in sorted(container.keys(), key=lambda k: int(k.replace("block", "")) if k.startswith("block") else k):
            yield key, container[key]
    else:
        for idx, block in enumerate(container):
            yield str(idx), block


def _detect_offloadable(diffusion_model) -> list[str]:
    # Return attribute names of offloadable side-components that exist on the model.
    found = []
    for attr in _KNOWN_OFFLOADABLE:
        component = getattr(diffusion_model, attr, None)
        if component is not None and isinstance(component, nn.Module):
            found.append(attr)
    return found


def _get_model_arch_name(model_patcher) -> str:
    # Return a human-friendly architecture name for logging.
    base_model = model_patcher.model
    class_name = type(base_model).__name__
    # Also include diffusion model class for clarity
    diff_model = getattr(base_model, "diffusion_model", None)
    if diff_model is not None:
        diff_name = type(diff_model).__name__
        return f"{class_name}/{diff_name}"
    return class_name


# ── Block offloading via comfy_cast_weights ──────────────────────────
#
# Instead of forward hooks that call module.to(gpu) / module.to(cpu)
# on every forward pass (which conflicts with CUDA async operations and
# causes "CUDA error: invalid argument"), we leverage ComfyUI's native
# lowvram system:
#
# 1. Move the block's parameters to CPU
# 2. Set comfy_cast_weights=True on every leaf module that supports it
#    (Linear, Conv, LayerNorm, RMSNorm, etc. — all inherit CastWeightBiasOp)
# 3. Pin CPU weights via model_patcher.pin_weight_to_device() for faster
#    async GPU transfers — tracked in model_patcher.pinned so
#    unpin_all_weights() cleans them up on model unload
#
# During forward, each leaf module's forward() routes through
# forward_comfy_cast_weights() → cast_bias_weight() which temporarily
# copies the weight to GPU, runs the operation, then releases.
# No block-level .to() calls happen during inference.
#
# Cleanup is handled automatically by ComfyUI's unpatch_model() which
# calls wipe_lowvram_weight() to restore comfy_cast_weights,
# unpin_all_weights() to unpin our tracked keys, and then restores
# the original weight backups.

def _offload_module(module: nn.Module, offload_device: torch.device,
                    model_patcher=None, module_prefix: str = "") -> int:
    # Move module to CPU and enable ComfyUI's weight-casting lowvram system
    # on all leaf sub-modules.  Returns estimated bytes freed from GPU.
    #
    # model_patcher + module_prefix are used to pin weights via ComfyUI's
    # tracked pinning system (model_patcher.pin_weight_to_device) so they
    # get properly unpinned on model unload.

    # Estimate GPU memory before moving
    gpu_bytes = 0
    for p in module.parameters():
        if p.device.type != 'cpu':
            gpu_bytes += p.nelement() * p.element_size()

    if gpu_bytes == 0:
        # Already on CPU — nothing to do
        return 0

    # Move all parameters and buffers to CPU
    module.to(offload_device)

    # Enable comfy_cast_weights on leaf modules so their forward() uses
    # the weight-casting path instead of standard pytorch (which would
    # fail with device mismatch).
    #
    # Also set comfy_patched_weights=False so ComfyUI's partially_unload()
    # and partially_load() know these modules are offloaded.
    cast_count = 0
    for child in module.modules():
        if hasattr(child, "comfy_cast_weights") and not child.comfy_cast_weights:
            # Save previous value so wipe_lowvram_weight() can restore it
            if not hasattr(child, "prev_comfy_cast_weights"):
                child.prev_comfy_cast_weights = child.comfy_cast_weights
            child.comfy_cast_weights = True
            cast_count += 1
        # Mark as offloaded so ComfyUI's load/unload system knows
        if hasattr(child, "comfy_patched_weights"):
            child.comfy_patched_weights = False

    # Pin CPU weights for faster async GPU transfers via offload streams.
    # Use model_patcher.pin_weight_to_device() which tracks the key in
    # model_patcher.pinned — so unpin_all_weights() cleans them up on
    # model unload.  Never call comfy.model_management.pin_memory()
    # directly — that bypasses tracking and leaves stale cudaHostRegister
    # entries that cause CUDA errors when loading the next model.
    pinned = 0
    if model_patcher is not None and module_prefix:
        for name, _param in module.named_parameters():
            key = f"{module_prefix}.{name}"
            try:
                model_patcher.pin_weight_to_device(key)
                pinned += 1
            except Exception:
                pass  # Key not found — skip (e.g. buffers, non-standard params)

    if cast_count > 0:
        log.debug(
            _LOG_PREFIX,
            f"  Enabled cast_weights on {cast_count} ops, "
            f"pinned {pinned} params, freed ~{gpu_bytes / (1024**2):.0f} MB"
        )

    return gpu_bytes


# ── ON_LOAD callback ─────────────────────────────────────────────────

def _make_swap_callback(blocks_to_swap: int, offload_embeddings: bool):
    # Create and return the ON_LOAD callback closure.
    #
    # This callback fires AFTER ComfyUI's load() has finished moving the
    # model to GPU.  We then move selected blocks back to CPU and activate
    # comfy_cast_weights on their leaf modules.
    def _swap_blocks(
        model_patcher,
        device_to,
        lowvram_model_memory,
        force_patch_weights,
        full_load
    ):
        base_model = model_patcher.model
        diff_model = getattr(base_model, "diffusion_model", None)
        if diff_model is None:
            log.warning(_LOG_PREFIX, "No diffusion_model found — skipping block swap")
            return

        # Nothing to swap
        if blocks_to_swap == 0:
            return

        # ComfyUI 0.18.0+ dynamic VRAM handles offloading natively
        if _is_native_dynamic_vram(model_patcher):
            log.debug(_LOG_PREFIX, "Native dynamic VRAM active — BlockSwap not needed")
            return

        # Detect architecture
        block_groups = _detect_block_groups(diff_model)
        if not block_groups:
            log.warning(_LOG_PREFIX, "No transformer block lists detected — skipping")
            return

        offload_device = model_patcher.offload_device
        total_blocks = _count_blocks(block_groups)
        actual_swap = min(blocks_to_swap, total_blocks)
        arch_name = _get_model_arch_name(model_patcher)

        # Guard: skip if blocks are already offloaded (duplicate callback)
        first_block = next(_iter_blocks(block_groups[0][1]))[1]
        first_device = next(first_block.parameters()).device
        if first_device == offload_device:
            log.debug(_LOG_PREFIX, "Blocks already offloaded — skipping duplicate callback")
            return

        log.msg(_LOG_PREFIX, f"Architecture: {arch_name}  |  "
                f"Total blocks: {total_blocks}  |  "
                f"Offloading {actual_swap} to {offload_device}")

        # Move first N blocks to CPU with comfy_cast_weights enabled.
        # Pass module_prefix so pin_weight_to_device() uses tracked keys.
        offloaded = 0
        total_freed = 0
        for attr_name, container in block_groups:
            for key_part, block in _iter_blocks(container):
                if offloaded < actual_swap:
                    prefix = f"diffusion_model.{attr_name}.{key_part}"
                    total_freed += _offload_module(
                        block, offload_device, model_patcher, prefix
                    )
                    offloaded += 1

        # Optionally offload embedding / projection layers
        if offload_embeddings:
            offloadable = _detect_offloadable(diff_model)
            if offloadable:
                log.msg(_LOG_PREFIX, f"Offloading {len(offloadable)} side components: "
                        f"{', '.join(offloadable)}")
                for attr_name in offloadable:
                    component = getattr(diff_model, attr_name)
                    prefix = f"diffusion_model.{attr_name}"
                    total_freed += _offload_module(
                        component, offload_device, model_patcher, prefix
                    )

        # Update ComfyUI's memory accounting so it knows we freed VRAM.
        # Without this, ComfyUI's model_management thinks the model still
        # occupies the original GPU footprint and makes wrong decisions
        # when loading other models (e.g. controlnets), causing OOM.
        if total_freed > 0:
            base_model.model_loaded_weight_memory = max(
                0, base_model.model_loaded_weight_memory - total_freed
            )
            base_model.model_lowvram = True

        log.msg(_LOG_PREFIX, f"Offloaded {offloaded} blocks, "
                f"freed ~{total_freed / (1024**2):.0f} MB VRAM  |  "
                f"reported loaded: {base_model.model_loaded_weight_memory / (1024**2):.0f} MB")

        # Release cached VRAM back to OS
        comfy.model_management.soft_empty_cache()
        gc.collect()

    return _swap_blocks


# ── Node ──────────────────────────────────────────────────────────────

class RvTools_BlockSwap(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Universal Block Swap [Eclipse]",
            display_name="Universal Block Swap",
            description=(
                "Offloads transformer blocks from GPU to CPU to reduce VRAM usage. "
                "Uses ComfyUI's native weight-casting system — each operation "
                "temporarily loads its weights to GPU, runs, then releases. "
                "Works with WAN, Flux, Chroma, SD3, LTXV, HunyuanVideo, Cosmos, "
                "ZImage/NextDiT, and other DiT architectures. "
                "Connect before sampling — the swap happens when ComfyUI loads "
                "the model to GPU. "
                "NOTE: Obsolete on ComfyUI 0.18+. Dynamic VRAM management handles "
                "weight offloading natively, making BlockSwap redundant and "
                "incompatible. This node auto-detects dynamic VRAM and skips itself."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=[
                io.Model.Input("model", tooltip="The diffusion model to apply block swapping to."),
                io.Int.Input(
                    "blocks_to_swap",
                    default=10,
                    min=0,
                    max=100,
                    step=1,
                    tooltip=(
                        "Number of transformer blocks to offload from GPU to CPU. "
                        "Higher = more VRAM saved but slower inference. "
                        "Suggested ~value (max total blocks): "
                        "flux/chroma ~10 (max 57), sd3 ~8 (max 24-38), "
                        "wan ~10 (max 30-40), hunyuan-video ~10 (max 60), "
                        "ltxv ~6 (max 28), cosmos ~8 (max 28-36), "
                        "zimage ~10 (max 30), qwenimage ~20 (max 60), "
                        "mochi ~10 (max 48), hidream ~10 (max 48). "
                        "Set to 0 to disable."
                    ),
                ),
                io.Boolean.Input(
                    "offload_embeddings",
                    default=False,
                    label_on="Yes",
                    label_off="No",
                    tooltip=(
                        "Also offload embedding and projection layers (text_embedding, "
                        "img_emb, time_in, etc.). Saves a small amount of extra VRAM."
                    ),
                ),
            ],
            outputs=[
                io.Model.Output("model", tooltip="Model with block swap callback attached."),
            ],
        )

    @classmethod
    def execute(cls, model, blocks_to_swap, offload_embeddings):
        # Peek at architecture to give user feedback at queue time
        diff_model = getattr(model.model, "diffusion_model", None)
        if blocks_to_swap == 0:
            log.msg(_LOG_PREFIX, "blocks_to_swap=0 — no offloading")
        elif diff_model is not None:
            groups = _detect_block_groups(diff_model)
            total = _count_blocks(groups)
            arch = _get_model_arch_name(model)
            if total > 0:
                actual = min(blocks_to_swap, total)
                log.msg(_LOG_PREFIX, f"Detected {arch}: {total} blocks, "
                        f"will offload {actual} on next load")
                if blocks_to_swap > total:
                    log.warning(_LOG_PREFIX,
                                f"Requested {blocks_to_swap} but model only has {total} blocks — "
                                f"clamping to {total}")
            else:
                log.warning(_LOG_PREFIX,
                            f"Model {arch} has no recognized block structure — "
                            f"swap may have no effect")

        # ComfyUI 0.18.0+ dynamic VRAM makes BlockSwap redundant
        if _is_native_dynamic_vram(model):
            log.msg(_LOG_PREFIX, "Native dynamic VRAM detected — BlockSwap not needed, "
                    "passing model through")
            return io.NodeOutput(model)

        # Clone and register the ON_LOAD callback.
        # The callback fires after ComfyUI's load() so we can move blocks
        # that were just loaded to GPU back to CPU with cast_weights enabled.
        # Cleanup (restoring comfy_cast_weights, unpinning) is handled
        # automatically by ComfyUI's unpatch_model() → wipe_lowvram_weight().
        patched = model.clone()
        patched.add_callback(
            CallbacksMP.ON_LOAD,
            _make_swap_callback(blocks_to_swap, offload_embeddings),
        )

        log.msg(_LOG_PREFIX, "Block swap callback registered")
        return io.NodeOutput(patched)
