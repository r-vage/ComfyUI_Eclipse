import os
import json
import random
import time
import torch  # type: ignore
import numpy as np  # type: ignore
import nodes  # type: ignore
import folder_paths  # type: ignore
import comfy.utils  # type: ignore

from PIL import Image  # type: ignore
from PIL.PngImagePlugin import PngInfo  # type: ignore
from typing import List, Optional
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.image_helpers import flatten_images, cat_and_fit_images

_LOG_PREFIX = "Image Selector"

# Per-session temp prefix (cache-busting)
_temp_dir = folder_paths.get_temp_directory()
_prefix_append = "_temp_" + "".join(
    random.choice("abcdefghijklmnopqrstupvxyz") for _ in range(5)
)

# ============================================================================
# Module-level shared state (lives for the lifetime of the ComfyUI process)
# ============================================================================

# unique_id → list[Tensor[1,H,W,C]]  — stored image list for each waiting node
_stored_images: dict = {}

# unique_id → list[int] | None
#   None  = no selection confirmed yet (node is waiting)
#   list  = confirmed indices, ready to consume on next run
_selections: dict = {}

# unique_id → str (stored signature of the images when selection was active)
_stored_signatures: dict = {}

# unique_id -> list[dict] (cached preview metadata)
_stored_ui_images: dict = {}

# unique_ids whose current selection was produced by auto-select mode. This
# closes the small frontend-reset race when the checkbox is turned off and a
# new prompt is queued before the reset request reaches the server.
_auto_selected_uids: set[str] = set()


def store_images(uid: str, images: list) -> None:
    _stored_images[uid] = images


def get_stored_images(uid) -> Optional[list]:
    return _stored_images.get(str(uid))


def store_selection(uid, indices: list) -> None:
    _selections[str(uid)] = indices


def get_selection(uid) -> Optional[list]:
    return _selections.get(str(uid))


def reset_selection(uid) -> None:
    uid_str = str(uid)
    _selections.pop(uid_str, None)
    _auto_selected_uids.discard(uid_str)


def clear_state(uid) -> None:
    uid_str = str(uid)
    _stored_images.pop(uid_str, None)
    _selections.pop(uid_str, None)
    _stored_signatures.pop(uid_str, None)
    _stored_ui_images.pop(uid_str, None)
    _auto_selected_uids.discard(uid_str)
    subfolder = f"_cache_selector/{uid_str}"
    full_folder = os.path.join(_temp_dir, subfolder)
    if os.path.exists(full_folder):
        try:
            import shutil

            shutil.rmtree(full_folder)
        except Exception:
            pass


# ============================================================================
# Helpers
# ============================================================================





def _compute_signature(image_list: list) -> str:
    # Fast stable signature using tensor shapes, length, and sampling select image stats
    import hashlib

    if not image_list:
        return "empty"

    sig_parts = []
    sig_parts.append(f"len:{len(image_list)}")

    # Add shapes of all tensors
    for idx, img in enumerate(image_list):
        if not isinstance(img, torch.Tensor):
            sig_parts.append(f"{idx}:non-tensor")
            continue
        if img.numel() == 0:
            sig_parts.append(f"{idx}:empty")
            continue
        shape_str = "x".join(str(s) for s in img.shape)
        sig_parts.append(f"{idx}:{shape_str}")

    # Sample stats (sum and mean) from first, middle, and last images for content validation
    sample_indices = [0]
    if len(image_list) > 1:
        sample_indices.append(len(image_list) // 2)
        sample_indices.append(len(image_list) - 1)

    # Remove duplicates if list is very small
    sample_indices = sorted(list(set(sample_indices)))

    for idx in sample_indices:
        img = image_list[idx]
        if isinstance(img, torch.Tensor) and img.numel() > 0:
            try:
                img_sum = float(img.sum())
                img_mean = float(img.mean())
                # Use 4 decimal places for stable floating point string representation
                sig_parts.append(f"stats_{idx}:{img_sum:.4f}:{img_mean:.4f}")
            except Exception:
                pass

    return hashlib.md5("|".join(sig_parts).encode()).hexdigest()


def _save_previews(image_list: list, prompt, extra_pnginfo, uid: str) -> list:
    # Save each [1,H,W,C] tensor to temp dir. Returns list of {filename, subfolder, type}.
    import shutil

    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo is not None:
        for k in extra_pnginfo:
            metadata.add_text(k, json.dumps(extra_pnginfo[k]))

    subfolder = f"_cache_selector/{uid}"
    full_folder = os.path.join(_temp_dir, subfolder)

    # Remove old cache directory to avoid bloating
    if os.path.exists(full_folder):
        try:
            shutil.rmtree(full_folder)
        except Exception as e:
            log.warning(
                _LOG_PREFIX,
                f"[{uid}] Failed to clean up old cache directory {full_folder}: {e}",
            )

    os.makedirs(full_folder, exist_ok=True)

    results = []
    pbar = comfy.utils.ProgressBar(len(image_list))
    for idx, img_t in enumerate(image_list):
        frame = img_t[0] if img_t.dim() == 4 else img_t
        arr = np.clip(255.0 * frame.cpu().numpy(), 0, 255).astype(np.uint8)
        pil = Image.fromarray(arr)

        # Scale down preview to max 1024px to save browser memory/VRAM and disk space
        max_size = 1024
        if pil.width > max_size or pil.height > max_size:
            if pil.width > pil.height:
                new_w = max_size
                new_h = int(pil.height * (max_size / pil.width))
            else:
                new_h = max_size
                new_w = int(pil.width * (max_size / pil.height))

            if hasattr(Image, "Resampling"):
                method = Image.Resampling.LANCZOS
            else:
                method = getattr(Image, "LANCZOS")
            pil = pil.resize((new_w, new_h), method)

        ts = int(time.time() * 1000) % 100000000
        fname = f"preview_{idx:05}_{ts}.png"
        pil.save(os.path.join(full_folder, fname), pnginfo=metadata, compress_level=1)
        results.append({"filename": fname, "subfolder": subfolder, "type": "temp"})
        pbar.update(1)

    return results





# ============================================================================
# Node class
# ============================================================================


class RvImage_Selector(io.ComfyNode):
    # Interactive image selector.
    #
    # MANUAL FIRST RUN:
    #   - Saves all images as temp previews
    #   - Sends them to the UI with eclipseSelector=True so the JS renders
    #     the selection overlay (checkboxes, Confirm / Discard toolbar)
    #   - Interrupts the workflow (same as the Stop node)
    #   - State is stored server-side by unique_id
    #
    # USER ACTION:
    #   - Clicks images to select, then "Confirm" in the JS toolbar
    #   - JS POSTs the selected indices to /eclipse/image_selector/confirm
    #   - Confirm automatically re-queues the workflow
    #   - Clicking "Discard" clears the decision (next queue waits again)
    #
    # SECOND+ RUN (after manual re-queue):
    #   - Detects stored selection → outputs selected images using the (cached) incoming batch
    #   - Frees stored tensor memory but keeps _selections so subsequent re-queues reuse it
    #   - Selection persists until the input changes or the user clicks Discard
    #
    # AUTO MODE:
    #   - Selects every incoming image and renders the same grid with all items selected
    #   - Returns immediately without interrupting; unchecking clears the decision

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Selector [Eclipse]",
            display_name="Image Selector",
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE_BATCH.value,
            description=(
                "Interactive image selector. On first run, shows all images and pauses the workflow. "
                "Click to toggle · Shift+click for range · Ctrl+A select all · Esc clear. "
                "Confirm auto-requeues the workflow. Enable Auto select and confirm in the selector "
                "toolbar to pass every incoming image without pausing. Outputs selected images as a "
                "batch and their indices."
            ),
            is_output_node=True,
            inputs=[
                io.Image.Input(
                    "images",
                    tooltip="Image batch [N,H,W,C] or list of images. All sizes are supported.",
                ),
                io.Int.Input(
                    "execution_trigger",
                    default=0,
                    min=0,
                    max=2147483647,
                    step=1,
                    socketless=True,
                    tooltip="Internal re-execution counter. Updated automatically by the UI on Confirm. Do not modify manually.",
                ),
                io.Boolean.Input(
                    "auto_select_and_confirm",
                    default=False,
                    socketless=True,
                    tooltip="Internal state for the selector toolbar's Auto select and confirm checkbox.",
                ),
            ],
            outputs=[
                io.Image.Output(
                    "images",
                    tooltip="Selected images stacked into a batch [N,H,W,C]. "
                    "All resized to first selected image's dimensions.",
                ),
                io.Custom("LIST").Output(
                    "indices",
                    tooltip="Confirmed selected indices.",
                ),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo],
            is_input_list=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        import hashlib
        import uuid
        import inspect

        # Inspect stack to find the ComfyUI node_id, dynprompt, and outputs_cache
        node_id = None
        dynprompt = None
        outputs_cache = None

        curr = inspect.currentframe()
        while curr:
            locs = curr.f_locals
            if "node_id" in locs and isinstance(locs["node_id"], (str, int)):
                node_id = str(locs["node_id"])
            if "self" in locs:
                obj = locs["self"]
                if type(obj).__name__ == "IsChangedCache":
                    dynprompt = getattr(obj, "dynprompt", None)
                    outputs_cache = getattr(obj, "outputs_cache", None)
            if node_id is not None and dynprompt is not None and outputs_cache is not None:
                break
            curr = curr.f_back

        selection = get_selection(node_id) if node_id is not None else None
        if selection is None or len(selection) == 0:
            # Force re-execution on every queue run if no selection confirmed yet
            return str(uuid.uuid4())

        trigger = kwargs.get("execution_trigger", 0)
        if isinstance(trigger, list):
            trigger = trigger[0]

        auto_select = kwargs.get("auto_select_and_confirm", False)
        if isinstance(auto_select, list):
            auto_select = auto_select[0] if auto_select else False
        auto_select = bool(auto_select)

        # Dynamically lookup the upstream images to check if their content actually changed
        image_sig = ""
        if node_id is not None and dynprompt is not None and outputs_cache is not None:
            try:
                node = dynprompt.get_node(node_id)
                images_input = node.get("inputs", {}).get("images")
                # Connection format is: [upstream_node_id, output_index]
                if isinstance(images_input, (list, tuple)) and len(images_input) == 2:
                    upstream_node_id = str(images_input[0])
                    output_index = int(images_input[1])
                    cached_entry = outputs_cache.get_local(upstream_node_id)
                    if cached_entry is not None and hasattr(cached_entry, "outputs"):
                        upstream_outputs = cached_entry.outputs
                        if isinstance(upstream_outputs, (list, tuple)) and len(upstream_outputs) > output_index:
                            images_tensor = upstream_outputs[output_index]
                            if images_tensor is not None:
                                normalized = flatten_images(images_tensor)
                                image_sig = _compute_signature(normalized)
            except Exception as e:
                # Fall back gracefully to avoid breaking the execution flow
                log.warning(_LOG_PREFIX, f"[{node_id}] Failed to compute upstream image signature in fingerprint_inputs: {e}")

        # Generate fingerprint which changes if trigger, selection, or upstream image content changes
        return hashlib.md5(
            f"{trigger}_{auto_select}_{selection}_{image_sig}".encode()
        ).hexdigest()

    @classmethod
    def execute(cls, images, execution_trigger=0, auto_select_and_confirm=False):
        if isinstance(images, list) and len(images) == 1:
            images = images[0]

        if isinstance(auto_select_and_confirm, list):
            auto_select_and_confirm = (
                auto_select_and_confirm[0] if auto_select_and_confirm else False
            )
        auto_select_and_confirm = bool(auto_select_and_confirm)

        uid = str(cls.hidden.unique_id)
        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo

        image_list = flatten_images(images)
        current_sig = _compute_signature(image_list)

        selection = get_selection(uid)

        # Check if input images changed since we stored them (even if selection is None/not confirmed yet)
        stored_sig = _stored_signatures.get(uid)
        if stored_sig is not None and stored_sig != current_sig:
            log.msg(
                _LOG_PREFIX,
                f"[{uid}] Input images changed (signature mismatch: stored={stored_sig} current={current_sig}) — auto-discarding selection and state",
            )
            clear_state(uid)
            selection = None

        # Unchecking Auto select and confirm is a discard-like action. The
        # frontend resets immediately, and this server-side marker guarantees
        # that a prompt queued during that request cannot reuse the old
        # automatic selection.
        if not auto_select_and_confirm and uid in _auto_selected_uids:
            reset_selection(uid)
            selection = None

        # Determine if we already ran the first run on these images (stored sig matches and stored images exist)
        already_interrupted = (
            stored_sig is not None
            and stored_sig == current_sig
            and get_stored_images(uid) is not None
        )

        if auto_select_and_confirm:
            valid = list(range(len(image_list)))
            batch = cat_and_fit_images(image_list, log_prefix=_LOG_PREFIX)
            cached_ui_images = (
                _stored_ui_images.get(uid) if stored_sig == current_sig else None
            )
            if cached_ui_images is None:
                cached_ui_images = _save_previews(
                    image_list, prompt, extra_pnginfo, uid
                )
                _stored_ui_images[uid] = cached_ui_images

            store_selection(uid, valid)
            _auto_selected_uids.add(uid)
            _stored_signatures[uid] = current_sig
            _stored_images.pop(uid, None)

            log.msg(
                _LOG_PREFIX,
                f"[{uid}] Auto-selected all {len(valid)} image(s) without interrupting",
            )
            return io.NodeOutput(
                batch,
                valid,
                ui={
                    "images": cached_ui_images,
                    "eclipseSelector": [True],
                    "autoSelectAndConfirm": [True],
                    "selectedIndices": [valid],
                    "totalCount": [len(image_list)],
                },
            )

        if selection is not None:
            # User confirmed a selection
            valid = [i for i in selection if 0 <= i < len(image_list)]
            if valid:
                selected_list = [image_list[i] for i in valid]
                batch = cat_and_fit_images(selected_list, log_prefix=_LOG_PREFIX)

                out_pipe = {}
                out_pipe["image_list"] = selected_list
                out_pipe["image"] = batch
                out_pipe["selected_indices"] = valid

                ui_images = _save_previews(selected_list, prompt, extra_pnginfo, uid)
                _stored_images.pop(uid, None)  # Free stored images tensor cache

                count = len(selected_list)
                log.msg(
                    _LOG_PREFIX,
                    f"[{uid}] Outputting {count} selected image(s) (selection confirmed)",
                )
                return io.NodeOutput(
                    batch,
                    valid,
                    ui={
                        "images": ui_images,
                        "eclipseSelector": [False],
                        "selectionCount": [count],
                    },
                )
            else:
                log.msg(
                    _LOG_PREFIX,
                    f"[{uid}] Confirmed selection is empty — interrupting workflow",
                )

        # No selection confirmed (first run or empty selection confirmed)
        valid = list(range(len(image_list)))
        selected_list = image_list
        batch = cat_and_fit_images(selected_list, log_prefix=_LOG_PREFIX)

        out_pipe = {}
        out_pipe["image_list"] = selected_list
        out_pipe["image"] = batch
        out_pipe["selected_indices"] = valid

        # Cache inputs & signature
        store_images(uid, image_list)
        _stored_signatures[uid] = current_sig

        if already_interrupted and uid in _stored_ui_images:
            ui_images = _stored_ui_images[uid]
        else:
            ui_images = _save_previews(image_list, prompt, extra_pnginfo, uid)
            _stored_ui_images[uid] = ui_images

        log.msg(
            _LOG_PREFIX,
            f"[{uid}] No selection confirmed — interrupting workflow to await selection",
        )
        nodes.interrupt_processing()

        return io.NodeOutput(
            batch,
            valid,
            ui={
                "images": ui_images,
                "eclipseSelector": [True],
                "totalCount": [len(image_list)],
            },
        )
