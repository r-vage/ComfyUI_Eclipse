# Shared Universal BlockSwap implementation for DiT diffusion models.

import gc

import comfy.model_management  # type: ignore
import torch  # type: ignore
from comfy.patcher_extension import CallbacksMP  # type: ignore
from torch import nn  # type: ignore

from ..logger import log

_LOG_PREFIX = "BlockSwap"

_KNOWN_BLOCK_ATTRS = (
    "double_blocks",
    "single_blocks",
    "double_stream_blocks",
    "single_stream_blocks",
    "blocks",
    "transformer_blocks",
    "layers",
    "joint_blocks",
)
_KNOWN_OFFLOADABLE = (
    "text_embedding",
    "img_emb",
    "img_in",
    "txt_in",
    "time_in",
    "vector_in",
    "guidance_in",
    "x_embedder",
    "cap_embedder",
    "t_embedder",
    "y_embedder",
    "time_text_embed",
    "txt_norm",
    "noise_refiner",
    "context_refiner",
    "context_embedder",
    "patch_embedding",
    "time_embedding",
    "head",
    "final_layer",
    "norm_out",
    "proj_out",
)


def is_native_dynamic_vram(model_patcher) -> bool:
    return model_patcher.is_dynamic() and hasattr(model_patcher, "backup_buffers")


def detect_block_groups(diffusion_model) -> list[tuple[str, nn.Module]]:
    groups: list[tuple[str, nn.Module]] = []
    for attr in _KNOWN_BLOCK_ATTRS:
        container = getattr(diffusion_model, attr, None)
        if isinstance(container, (nn.ModuleList, nn.ModuleDict)) and len(container) > 0:
            groups.append((attr, container))
    return groups


def count_blocks(groups: list[tuple[str, nn.Module]]) -> int:
    return sum(len(container) for _, container in groups)


def iter_blocks(container: nn.Module):
    if isinstance(container, nn.ModuleDict):
        def block_key(value: str) -> tuple[int, int | str]:
            suffix = value.removeprefix("block")
            return (0, int(suffix)) if suffix.isdigit() else (1, value)

        for key in sorted(container.keys(), key=block_key):
            yield key, container[key]
    else:
        for index, block in enumerate(container):
            yield str(index), block


def detect_offloadable(diffusion_model) -> list[str]:
    return [
        attr
        for attr in _KNOWN_OFFLOADABLE
        if isinstance(getattr(diffusion_model, attr, None), nn.Module)
    ]


def get_model_arch_name(model_patcher) -> str:
    base_model = model_patcher.model
    class_name = type(base_model).__name__
    diffusion_model = getattr(base_model, "diffusion_model", None)
    if diffusion_model is None:
        return class_name
    return f"{class_name}/{type(diffusion_model).__name__}"


def offload_module(
    module: nn.Module,
    offload_device: torch.device,
    model_patcher=None,
    module_prefix: str = "",
) -> int:
    gpu_bytes = sum(
        parameter.nelement() * parameter.element_size()
        for parameter in module.parameters()
        if parameter.device.type != "cpu"
    )
    if gpu_bytes == 0:
        return 0

    module.to(offload_device)
    cast_count = 0
    for child in module.modules():
        if hasattr(child, "comfy_cast_weights") and not child.comfy_cast_weights:
            if not hasattr(child, "prev_comfy_cast_weights"):
                child.prev_comfy_cast_weights = child.comfy_cast_weights
            child.comfy_cast_weights = True
            cast_count += 1
        if hasattr(child, "comfy_patched_weights"):
            child.comfy_patched_weights = False

    pinned = 0
    if model_patcher is not None and module_prefix:
        for name, _parameter in module.named_parameters():
            try:
                model_patcher.pin_weight_to_device(f"{module_prefix}.{name}")
                pinned += 1
            except (AttributeError, KeyError, RuntimeError, ValueError):
                # Non-standard buffers may not have ModelPatcher keys.
                continue

    if cast_count:
        log.debug(
            _LOG_PREFIX,
            f"Enabled cast_weights on {cast_count} ops, pinned {pinned} params, "
            f"freed ~{gpu_bytes / (1024**2):.0f} MB",
        )
    return gpu_bytes


def make_swap_callback(blocks_to_swap: int, offload_embeddings: bool):
    if isinstance(blocks_to_swap, bool) or not isinstance(blocks_to_swap, int):
        raise TypeError("blocks_to_swap must be an integer")
    if not 0 <= blocks_to_swap <= 100:
        raise ValueError("blocks_to_swap is outside the supported range")
    if not isinstance(offload_embeddings, bool):
        raise TypeError("offload_embeddings must be true or false")

    def swap_blocks(
        model_patcher,
        device_to,
        lowvram_model_memory,
        force_patch_weights,
        full_load,
    ):
        del device_to, lowvram_model_memory, force_patch_weights, full_load
        diffusion_model = getattr(model_patcher.model, "diffusion_model", None)
        if diffusion_model is None or blocks_to_swap == 0:
            return
        if is_native_dynamic_vram(model_patcher):
            log.debug(_LOG_PREFIX, "Native dynamic VRAM active — BlockSwap not needed")
            return

        groups = detect_block_groups(diffusion_model)
        if not groups:
            log.warning(_LOG_PREFIX, "No transformer block lists detected — skipping")
            return
        first_block = next(iter_blocks(groups[0][1]))[1]
        first_parameter = next(first_block.parameters(), None)
        if first_parameter is not None and first_parameter.device == model_patcher.offload_device:
            log.debug(_LOG_PREFIX, "Blocks already offloaded — skipping duplicate callback")
            return

        total_blocks = count_blocks(groups)
        actual_swap = min(blocks_to_swap, total_blocks)
        log.msg(
            _LOG_PREFIX,
            f"Architecture: {get_model_arch_name(model_patcher)} | "
            f"Total blocks: {total_blocks} | Offloading {actual_swap}",
        )

        offloaded = 0
        total_freed = 0
        for attr_name, container in groups:
            for key_part, block in iter_blocks(container):
                if offloaded >= actual_swap:
                    break
                total_freed += offload_module(
                    block,
                    model_patcher.offload_device,
                    model_patcher,
                    f"diffusion_model.{attr_name}.{key_part}",
                )
                offloaded += 1

        if offload_embeddings:
            for attr_name in detect_offloadable(diffusion_model):
                total_freed += offload_module(
                    getattr(diffusion_model, attr_name),
                    model_patcher.offload_device,
                    model_patcher,
                    f"diffusion_model.{attr_name}",
                )

        base_model = model_patcher.model
        if total_freed > 0:
            loaded_memory = getattr(base_model, "model_loaded_weight_memory", 0)
            base_model.model_loaded_weight_memory = max(0, loaded_memory - total_freed)
            base_model.model_lowvram = True
        log.msg(
            _LOG_PREFIX,
            f"Offloaded {offloaded} blocks, freed ~{total_freed / (1024**2):.0f} MB VRAM",
        )
        comfy.model_management.soft_empty_cache()
        gc.collect()

    return swap_blocks


def apply_blockswap(
    model,
    blocks_to_swap: int,
    offload_embeddings: bool,
    log_prefix: str,
    *,
    is_nunchaku: bool = False,
    is_qwen: bool = False,
    is_zimage: bool = False,
):
    if model is None or blocks_to_swap <= 0:
        return model
    if is_nunchaku or is_qwen or is_zimage:
        return model
    if is_native_dynamic_vram(model):
        log.msg(log_prefix, "BlockSwap: native dynamic VRAM active — not needed")
        return model

    diffusion_model = getattr(model.model, "diffusion_model", None)
    groups = detect_block_groups(diffusion_model) if diffusion_model is not None else []
    total = count_blocks(groups)
    architecture = get_model_arch_name(model)
    if total == 0:
        log.warning(log_prefix, f"BlockSwap: {architecture} has no recognized block structure — skipping")
        return model

    actual = min(blocks_to_swap, total)
    log.msg(
        log_prefix,
        f"BlockSwap: {architecture} — {total} blocks, will offload {actual} on next load",
    )
    patched = model.clone()
    patched.add_callback(
        CallbacksMP.ON_LOAD,
        make_swap_callback(blocks_to_swap, offload_embeddings),
    )
    return patched


# Compatibility aliases for callers that used the former private node helpers.
_is_native_dynamic_vram = is_native_dynamic_vram
_detect_block_groups = detect_block_groups
_count_blocks = count_blocks
_iter_blocks = iter_blocks
_detect_offloadable = detect_offloadable
_get_model_arch_name = get_model_arch_name
_offload_module = offload_module
_make_swap_callback = make_swap_callback
