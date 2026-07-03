import os
import comfy  # type: ignore
import comfy.sd  # type: ignore
import folder_paths  # type: ignore

from ...core import CATEGORY
from ...core.logger import log
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "Checkpoint Loader"
class RvLoader_Checkpoint_Loader_Small(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Checkpoint Loader Small [Eclipse]",
            display_name="⚠ Checkpoint Loader Small",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Combo.Input("ckpt_name", options=folder_paths.get_filename_list("checkpoints"), tooltip="Select the checkpoint filename to load (e.g. my_model.ckpt)."),
                io.Combo.Input("vae_name", options=["Baked VAE"] + folder_paths.get_filename_list("vae"), tooltip="Select a VAE file or 'Baked VAE' to use the embedded VAE from the checkpoint."),
                io.Int.Input("stop_at_clip_layer", default=-2, min=-24, max=-1, step=1, tooltip="Negative index for CLIP layer to stop at. -1 keeps full CLIP."),
            ],
            outputs=[
                io.Custom("MODEL").Output("model"),
                io.Custom("CLIP").Output("clip"),
                io.Custom("VAE").Output("vae"),
                io.String.Output("model_name"),
            ],
        )

    @classmethod
    def execute(cls, ckpt_name: str, vae_name: str, stop_at_clip_layer: int) -> io.NodeOutput:
        # Load a checkpoint with lightweight type validation and performance tweaks.

        if not isinstance(ckpt_name, str) or not isinstance(vae_name, str):
            raise TypeError("ckpt_name and vae_name must be strings")
        if not isinstance(stop_at_clip_layer, int):
            raise TypeError("stop_at_clip_layer must be an int")

        try:
            # Resolve the checkpoint path via folder_paths helper. This keeps
            # compatibility with the host environment while allowing a safety
            # check below.
            ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
            if not ckpt_path:
                raise FileNotFoundError(f"Checkpoint not found: {ckpt_name}")

            # Moderate security checks (warnings rather than hard failures):
            # - discourage absolute paths and parent traversal but allow them
            #   with a warning (host might supply absolute paths in some setups)
            if os.path.isabs(ckpt_name):
                log.warning(_LOG_PREFIX, "Absolute checkpoint paths are discouraged")
            if ".." in ckpt_name.replace('\\', '/'):
                log.warning(_LOG_PREFIX, "Parent-traversal sequences detected in checkpoint name")

            # Ensure the resolved path is inside the configured checkpoints folder
            ckpt_path_abs = os.path.abspath(ckpt_path)
            try:
                checkpoints_base = folder_paths.get_folder_paths("checkpoints")
                # folder_paths may return a list/tuple or a single path
                if isinstance(checkpoints_base, (list, tuple)):
                    base_candidate = checkpoints_base[0] if len(checkpoints_base) > 0 else None
                else:
                    base_candidate = checkpoints_base

                if base_candidate:
                    checkpoints_base_abs = os.path.abspath(base_candidate)
                    # Use realpath + commonpath to avoid symlink escapes
                    try:
                        ckpt_path_real = os.path.realpath(ckpt_path_abs)
                        checkpoints_base_real = os.path.realpath(checkpoints_base_abs)
                        common = os.path.commonpath([checkpoints_base_real, ckpt_path_real])
                        if common != checkpoints_base_real:
                            log.warning(_LOG_PREFIX, "Resolved checkpoint path is outside the checkpoints folder")
                    except Exception:
                        # Fallback: best-effort startswith check
                        if not ckpt_path_abs.startswith(checkpoints_base_abs):
                            log.warning(_LOG_PREFIX, "Resolved checkpoint path is outside the checkpoints folder")
                else:
                    ckpt_path_real = os.path.realpath(ckpt_path_abs)
            except Exception:
                # If we can't verify base folder (older helper versions), continue
                ckpt_path_real = os.path.realpath(ckpt_path_abs)

            # Verify checkpoint file exists and is readable (moderate enforcement)
            if not os.path.isfile(ckpt_path_real) or not os.access(ckpt_path_real, os.R_OK):
                raise FileNotFoundError(f"Checkpoint file is not accessible: {ckpt_path_real}")

            # Extension guidance:
            # - Treat safetensors formats as the safe/modern format (.safetensors, .sft).
            # - Emit a warning for legacy/unsafe checkpoint extensions (.ckpt, .pt, .pth).
            safe_exts = {".safetensors", ".sft"}
            legacy_exts = {".ckpt", ".pt", ".pth"}
            _, ext = os.path.splitext(ckpt_path_real.lower())
            if ext:
                if ext in legacy_exts:
                    log.warning(_LOG_PREFIX, f"Legacy checkpoint extension detected: {ext}. Consider using .safetensors for improved safety.")
                elif ext not in safe_exts:
                    log.warning(_LOG_PREFIX, f"Unknown checkpoint extension: {ext}. Proceeding, but verify the file is a model.")

            output_vae = (vae_name == "Baked VAE")

            loaded_ckpt = comfy.sd.load_checkpoint_guess_config(
                ckpt_path_abs,
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
                vae_path_abs = os.path.abspath(vae_path)
                vae_path_real = os.path.realpath(vae_path_abs)
                if not os.path.isfile(vae_path_real) or not os.access(vae_path_real, os.R_OK):
                    raise FileNotFoundError(f"VAE file is not accessible: {vae_path_real}")
                sd_obj = comfy.utils.load_torch_file(vae_path_real)
                loaded_vae = comfy.sd.VAE(sd=sd_obj)

            # CLIP: avoid cloning unless we need to mutate (trim) it
            clip_candidate = ckpt_parts[1] if ckpt_parts is not None else getattr(loaded_ckpt, "clip", None)

            loaded_clip = None
            if clip_candidate is not None:
                if stop_at_clip_layer is not None and stop_at_clip_layer != -1:
                    # clone only when trimming is requested
                    try:
                        loaded_clip = clip_candidate.clone()
                        loaded_clip.clip_layer(stop_at_clip_layer)
                    except Exception:
                        # fall back to original if clone/trim fails
                        loaded_clip = clip_candidate
                else:
                    loaded_clip = clip_candidate

            model = ckpt_parts[0] if ckpt_parts is not None and len(ckpt_parts) >= 1 else getattr(loaded_ckpt, "model", None)

            return io.NodeOutput(model, loaded_clip, loaded_vae, ckpt_name)

        except Exception as e:
            log.error(_LOG_PREFIX, f"Checkpoint loading failed: {e}")
            # Fail-fast: don't return empty placeholders that allow downstream
            # nodes to continue sampling with a missing model. Raise so the
            # graph execution halts and surfaces the underlying error.
            raise RuntimeError(f"Checkpoint loading failed: {e}") from e