import os
import comfy  # type: ignore
import comfy.sd  # type: ignore
import folder_paths  # type: ignore

from ...core import CATEGORY
from ...core.logger import log
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Checkpoint Loader"
class RvLoader_Checkpoint_Loader_Small_Pipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Checkpoint Loader Small (Pipe) [Eclipse]",
            display_name="⚠ Checkpoint Loader Small (Pipe)",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Combo.Input("ckpt_name", options=folder_paths.get_filename_list("checkpoints"), tooltip="Select the checkpoint file to load (v1 format)."),
                io.Combo.Input("vae_name", options=["Baked VAE"] + folder_paths.get_filename_list("vae"), tooltip="Select a VAE or choose 'Baked VAE' to use the one embedded in the checkpoint."),
                io.Int.Input("stop_at_clip_layer", default=-2, min=-24, max=-1, step=1, tooltip="Negative index to stop CLIP at. -1 means full CLIP."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
        )

    @classmethod
    def execute(cls, ckpt_name: str, vae_name: str, stop_at_clip_layer: int) -> io.NodeOutput:
        # Input validation
        if not isinstance(ckpt_name, str) or not isinstance(vae_name, str):
            raise TypeError("ckpt_name and vae_name must be strings")
        if not isinstance(stop_at_clip_layer, int):
            raise TypeError("stop_at_clip_layer must be an int")

        try:
            ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
            if not ckpt_path:
                raise FileNotFoundError(f"Checkpoint not found: {ckpt_name}")

            # Moderate security: warn on absolute paths or parent traversal
            if os.path.isabs(ckpt_name):
                log.warning(_LOG_PREFIX, "Absolute checkpoint paths are discouraged")
            if ".." in ckpt_name.replace('\\', '/'):
                log.warning(_LOG_PREFIX, "Parent-traversal sequences detected in checkpoint name")

            # Resolve and verify checkpoint path
            ckpt_path_abs = os.path.abspath(ckpt_path)
            try:
                checkpoints_base = folder_paths.get_folder_paths("checkpoints")
                if isinstance(checkpoints_base, (list, tuple)):
                    base_candidate = checkpoints_base[0] if len(checkpoints_base) > 0 else None
                else:
                    base_candidate = checkpoints_base

                if base_candidate:
                    checkpoints_base_real = os.path.realpath(os.path.abspath(base_candidate))
                    ckpt_path_real = os.path.realpath(ckpt_path_abs)
                    try:
                        common = os.path.commonpath([checkpoints_base_real, ckpt_path_real])
                        if common != checkpoints_base_real:
                            log.warning(_LOG_PREFIX, "Resolved checkpoint path is outside the checkpoints folder")
                    except Exception:
                        if not ckpt_path_abs.startswith(checkpoints_base_real):
                            log.warning(_LOG_PREFIX, "Resolved checkpoint path is outside the checkpoints folder")
                else:
                    ckpt_path_real = os.path.realpath(ckpt_path_abs)
            except Exception:
                ckpt_path_real = os.path.realpath(ckpt_path_abs)

            # Ensure accessible
            if not os.path.isfile(ckpt_path_real) or not os.access(ckpt_path_real, os.R_OK):
                raise FileNotFoundError(f"Checkpoint file is not accessible: {ckpt_path_real}")

            # Extension handling: safetensors are considered safe; warn on legacy
            safe_exts = {".safetensors", ".sft"}
            legacy_exts = {".ckpt", ".pt", ".pth"}
            _, ext = os.path.splitext(ckpt_path_real.lower())
            if ext:
                if ext in legacy_exts:
                    log.warning(_LOG_PREFIX, f"Legacy checkpoint extension detected: {ext}. Consider using .safetensors")
                elif ext not in safe_exts:
                    log.warning(_LOG_PREFIX, f"Unknown checkpoint extension: {ext}. Proceeding, but verify file.")

            output_vae = (vae_name == "Baked VAE")

            loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
                ckpt_path_real,
                output_vae=output_vae,
                output_clip=True,
                embedding_directory=folder_paths.get_folder_paths("embeddings"),
            )

            # Cache commonly accessed checkpoint parts to avoid repeated slicing
            ckpt_parts = loaded_ckpt[:3] if hasattr(loaded_ckpt, '__len__') and len(loaded_ckpt) >= 3 else None

            # VAE extraction
            if vae_name == "Baked VAE":
                loaded_vae = ckpt_parts[2] if ckpt_parts is not None else None
            else:
                vae_path = folder_paths.get_full_path("vae", vae_name)
                if not vae_path:
                    raise FileNotFoundError(f"VAE not found: {vae_name}")
                vae_path_real = os.path.realpath(os.path.abspath(vae_path))
                if not os.path.isfile(vae_path_real) or not os.access(vae_path_real, os.R_OK):
                    raise FileNotFoundError(f"VAE file is not accessible: {vae_path_real}")
                sd_obj = comfy.utils.load_torch_file(vae_path_real)
                loaded_vae = comfy.sd.VAE(sd=sd_obj)

            # CLIP: avoid cloning unless trimming is requested
            clip_candidate = ckpt_parts[1] if ckpt_parts is not None else getattr(loaded_ckpt, "clip", None)

            loaded_clip = None
            if clip_candidate is not None:
                if stop_at_clip_layer is not None and stop_at_clip_layer != -1:
                    try:
                        loaded_clip = clip_candidate.clone()
                        loaded_clip.clip_layer(stop_at_clip_layer)
                    except Exception:
                        loaded_clip = clip_candidate
                else:
                    loaded_clip = clip_candidate

            # Build canonical dict-style pipe
            model_obj = ckpt_parts[0] if ckpt_parts is not None and len(ckpt_parts) >= 1 else (loaded_ckpt if loaded_ckpt is not None else None)
            pipe = {
                "model": model_obj,
                "clip": loaded_clip,
                "vae": loaded_vae,
                "latent": {"samples": None},
                "width": None,
                "height": None,
                "batch_size": 1,
                "model_name": ckpt_name,
                "vae_name": '' if vae_name == "Baked VAE" else str(vae_name),
                "clip_skip": int(stop_at_clip_layer),
            }
            return io.NodeOutput(pipe)

        except Exception as e:
            log.error(_LOG_PREFIX, f"Checkpoint loading failed: {e}")
            # Fail-fast: a missing or unreadable checkpoint should stop the graph
            # rather than returning a dummy pipe which can hide the root cause.
            raise RuntimeError(f"Checkpoint loading failed: {e}") from e
