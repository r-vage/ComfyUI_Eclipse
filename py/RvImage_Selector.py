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

_LOG_PREFIX = "Image Selector"

# Per-session temp prefix (cache-busting)
_temp_dir = folder_paths.get_temp_directory()
_prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for _ in range(5))

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


def store_images(uid: str, images: list) -> None:
    _stored_images[uid] = images


def get_stored_images(uid: str) -> Optional[list]:
    return _stored_images.get(uid)


def store_selection(uid: str, indices: list) -> None:
    _selections[uid] = indices


def get_selection(uid: str) -> Optional[list]:
    return _selections.get(uid)


def clear_state(uid: str) -> None:
    _stored_images.pop(uid, None)
    _selections.pop(uid, None)
    _stored_signatures.pop(uid, None)


# ============================================================================
# Helpers
# ============================================================================

def _normalize_to_list(images) -> List[torch.Tensor]:
    # Accept either a stacked [N,H,W,C] tensor or a Python list of [1,H,W,C] tensors.
    if isinstance(images, torch.Tensor) and images.dim() == 4:
        return [images[i:i+1] for i in range(images.shape[0])]
    if isinstance(images, (list, tuple)):
        out = []
        for img in images:
            if isinstance(img, torch.Tensor):
                if img.dim() == 3:
                    out.append(img.unsqueeze(0))
                else:
                    out.append(img)
        return out
    raise ValueError(f"Unsupported image input type: {type(images)}")


def _compute_signature(image_list: list) -> str:
    import hashlib
    sig_parts = [str(len(image_list))]
    for img in image_list:
        if not isinstance(img, torch.Tensor) or img.numel() == 0:
            continue
        shape_str = f"{img.shape[1]}x{img.shape[2]}x{img.shape[3]}"
        try:
            mean_val = float(img.mean())
            std_val = float(img.std()) if img.numel() > 1 else 0.0
        except Exception:
            mean_val = 0.0
            std_val = 0.0
        
        try:
            h, w = img.shape[1], img.shape[2]
            ch, cw = h // 2, w // 2
            patch = img[0, max(0, ch-4):min(h, ch+4), max(0, cw-4):min(w, cw+4)]
            patch_sum = float(patch.sum())
        except Exception:
            patch_sum = 0.0
            
        sig_parts.append(f"{shape_str}:{mean_val:.6f}:{std_val:.6f}:{patch_sum:.6f}")
    return hashlib.md5("|".join(sig_parts).encode()).hexdigest()


def _save_previews(image_list: list, prompt, extra_pnginfo) -> list:
    # Save each [1,H,W,C] tensor to temp dir. Returns list of {filename, subfolder, type}.
    metadata = PngInfo()
    if prompt is not None:
        metadata.add_text("prompt", json.dumps(prompt))
    if extra_pnginfo is not None:
        for k in extra_pnginfo:
            metadata.add_text(k, json.dumps(extra_pnginfo[k]))

    first = image_list[0]
    h, w = first.shape[1], first.shape[2]
    full_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
        "ComfyUI" + _prefix_append, _temp_dir, w, h)

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
        fname = f"{filename}_{counter + idx:05}_{ts}_.png"
        pil.save(os.path.join(full_folder, fname), pnginfo=metadata, compress_level=1)
        results.append({"filename": fname, "subfolder": subfolder, "type": "temp"})
        pbar.update(1)

    return results


def _resize_to_first(tensors: List[torch.Tensor]) -> torch.Tensor:
    # Stack tensors, resizing all to the first's H×W.
    target_h = tensors[0].shape[1]
    target_w = tensors[0].shape[2]
    out = []
    for t in tensors:
        if t.shape[1] != target_h or t.shape[2] != target_w:
            chw = t.permute(0, 3, 1, 2)
            chw = torch.nn.functional.interpolate(
                chw, size=(target_h, target_w), mode="bilinear", align_corners=False)
            t = chw.permute(0, 2, 3, 1)
        out.append(t)
    return torch.cat(out, dim=0)


# ============================================================================
# Node class
# ============================================================================

class RvImage_Selector(io.ComfyNode):
    # Interactive image selector.
    #
    # FIRST RUN:
    #   - Saves all images as temp previews
    #   - Sends them to the UI with eclipseSelector=True so the JS renders
    #     the selection overlay (checkboxes, Confirm / Discard toolbar)
    #   - Interrupts the workflow (same as the Stop node)
    #   - State is stored server-side by unique_id
    #
    # USER ACTION:
    #   - Clicks images to select, then "Confirm" in the JS toolbar
    #   - JS POSTs the selected indices to /eclipse/image_selector/confirm
    #   - No re-queue is triggered automatically — user does it manually
    #   - Clicking "Discard" clears server state (node reverts to first-run on next queue)
    #
    # SECOND+ RUN (after manual re-queue):
    #   - Detects stored selection → outputs selected images using the (cached) incoming batch
    #   - Frees stored tensor memory but keeps _selections so subsequent re-queues reuse it
    #   - Selection persists until user clicks "Re-select" in the UI (calls /discard)

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Image Selector [Eclipse]",
            display_name="Image Selector",
            description=(
                "Interactive image selector. On first run, shows all images and pauses the workflow. "
                "Click to toggle · Shift+click for range · Ctrl+A select all · Esc clear. "
                "Confirm auto-requeues the workflow. "
                "Outputs selected images as a batch (resized to first) and as a list (original sizes)."
            ),
            category=CATEGORY.MAIN.value + CATEGORY.IMAGE.value,
            is_output_node=True,
            inputs=[
                io.Image.Input("images",
                    tooltip="Image batch [N,H,W,C] or list of images. All sizes are supported."),
                io.Int.Input("execution_trigger", default=0, min=0, max=2147483647, step=1,
                    socketless=True,
                    tooltip="Internal re-execution counter. Updated automatically by the UI on Confirm. Do not modify manually."),
            ],
            outputs=[
                io.Image.Output("batch",
                    tooltip="Selected images stacked into a batch [N,H,W,C]. "
                            "All resized to first selected image's dimensions."),
                io.Image.Output("list",
                    tooltip="Selected images as a Python list — original sizes preserved."),
            ],
            hidden=[io.Hidden.unique_id, io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        import hashlib
        trigger = kwargs.get("execution_trigger", 0)
        images = kwargs.get("images")
        img_sig = ""
        if images is not None:
            if isinstance(images, torch.Tensor) and images.dim() == 4:
                img_sig = f"batch:{tuple(images.shape)}"
            elif isinstance(images, (list, tuple)) and images:
                img_sig = f"list:{len(images)}:{tuple(images[0].shape)}"
        return hashlib.md5(f"{trigger}|{img_sig}".encode()).hexdigest()

    @classmethod
    def execute(cls, images, execution_trigger=0):
        uid = cls.hidden.unique_id
        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo

        image_list = _normalize_to_list(images)
        current_sig = _compute_signature(image_list)

        selection = get_selection(uid)

        # Check if input images changed since we stored them (even if selection is None/not confirmed yet)
        stored_sig = _stored_signatures.get(uid)
        if stored_sig is not None and stored_sig != current_sig:
            log.msg(_LOG_PREFIX, f"[{uid}] Input images changed (signature mismatch) — auto-discarding selection and state")
            clear_state(uid)
            selection = None

        # Gating: stop execution if selector is active but no images are selected
        if selection is None or len(selection) == 0:
            if get_stored_images(uid) is not None:
                raise ValueError("Image Selector: No images selected. Please select at least one image to proceed.")

        # ── Second+ run: selection is waiting ─────────────────────────────────
        # Re-normalize from the incoming images input (upstream is cached — same data each run).
        # We don't need _stored_images on this path; free it if still held.
        if selection is not None:
            valid = [i for i in selection if 0 <= i < len(image_list)]
            if not valid:
                log.warning(_LOG_PREFIX, f"[{uid}] All stored indices out of bounds — reverting to first run")
                clear_state(uid)
            else:
                selected_list = [image_list[i] for i in valid]
                batch = _resize_to_first(selected_list)
                count = len(selected_list)

                ui_images = _save_previews(selected_list, prompt, extra_pnginfo)

                # Free tensor memory but keep selection and signature for future re-queues.
                _stored_images.pop(uid, None)

                log.msg(_LOG_PREFIX, f"[{uid}] Outputting {count} selected image(s) (selection preserved)")
                return io.NodeOutput(batch, selected_list,
                                     ui={"images": ui_images, "eclipseSelector": [False], "selectionCount": [count]})

        # ── First run: store images, interrupt, show selector UI ──────────────
        n = len(image_list)
        log.msg(_LOG_PREFIX, f"[{uid}] Storing {n} image(s), interrupting workflow for selection")

        store_images(uid, image_list)
        _stored_signatures[uid] = current_sig

        ui_images = _save_previews(image_list, prompt, extra_pnginfo)

        # Interrupt so downstream nodes don't execute yet
        nodes.interrupt_processing()

        # Empty tensors as placeholders — never consumed (workflow interrupted)
        empty_batch = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        return io.NodeOutput(
            empty_batch, [empty_batch],
            ui={"images": ui_images, "eclipseSelector": [True], "totalCount": [n]},
        )
