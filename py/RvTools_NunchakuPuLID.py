# Nunchaku PuLID nodes for Eclipse
#
# Provides PuLID Apply and Loader nodes that use Eclipse's vendored
# nunchaku glue code (ComfyFluxWrapper, copy_with_ctx), avoiding
# isinstance failures when the real ComfyUI-nunchaku node is also installed.
#
# Adapted from: ComfyUI-nunchaku/nodes/models/pulid.py
# Original license: Apache-2.0 (https://github.com/mit-han-lab/ComfyUI-nunchaku)

import os
import sys
from functools import partial

import numpy as np #type: ignore
import torch #type: ignore
import comfy.model_management #type: ignore

from comfy_api.latest import io #type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..extern.nunchaku.wrappers.flux import ComfyFluxWrapper, copy_with_ctx


_LOG_PREFIX = "NunchakuPuLID"

# Map user-facing provider names to nunchaku's onnx_provider parameter.
# Nunchaku only knows "gpu" and "cpu". For ROCm we pass "gpu" and let
# onnxruntime pick the available ROCm EP automatically.
_PROVIDER_MAP = {
    "CPU":  "cpu",
    "CUDA": "gpu",
    "ROCm": "gpu",
}

# ComfyUI folder_paths (handle both new and legacy import locations)
try:
    from comfy.cmd import folder_paths #type: ignore
    from comfy.model_downloader import get_filename_list, get_full_path_or_raise #type: ignore
except (ImportError, ModuleNotFoundError):
    folder_paths = sys.modules["folder_paths"]
    from folder_paths import get_filename_list, get_full_path_or_raise #type: ignore


def _ensure_model_path(key: str, subdir: str):
    # Register an extra model directory with ComfyUI if not already present.
    models_dir_default = os.path.join(folder_paths.models_dir, subdir)
    if key not in folder_paths.folder_names_and_paths:
        folder_paths.folder_names_and_paths[key] = (
            [models_dir_default],
            folder_paths.supported_pt_extensions,
        )
    else:
        if not os.path.exists(models_dir_default):
            os.makedirs(models_dir_default, exist_ok=True)
        folder_paths.add_model_folder_path(key, models_dir_default, is_default=True)


_ensure_model_path("pulid", "pulid")
_ensure_model_path("insightface", "insightface")
_ensure_model_path("facexlib", "facexlib")


class RvTools_NunchakuPuLIDLoader(io.ComfyNode):
    # Nunchaku PuLID Loader — loads the PuLID pipeline for a nunchaku FLUX model.

    @classmethod
    def define_schema(cls):
        pulid_files = get_filename_list("pulid")
        clip_files = get_filename_list("clip")
        return io.Schema(
            node_id="Nunchaku PuLID Loader [Eclipse]",
            display_name="Nunchaku PuLID Loader",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Load PuLID pipeline for Nunchaku FLUX models (Eclipse version).",
            inputs=[
                io.Model.Input("model", tooltip="The nunchaku FLUX model."),
                io.Combo.Input(
                    "pulid_file",
                    options=pulid_files,
                    tooltip="Path to the PuLID model file.",
                ),
                io.Combo.Input(
                    "eva_clip_file",
                    options=clip_files,
                    tooltip="Path to the EVA CLIP model file.",
                ),
                io.Combo.Input(
                    "insight_face_provider",
                    options=["CPU", "CUDA", "ROCm"],
                    default="CUDA",
                    tooltip="InsightFace ONNX provider (CPU, CUDA, or ROCm).",
                ),
            ],
            outputs=[
                io.Model.Output("model"),
                io.Custom("PULID_PIPELINE").Output("pulid_pipeline"),
            ],
        )

    @classmethod
    def execute(cls, model, pulid_file, eva_clip_file, insight_face_provider):
        from nunchaku.pipeline.pipeline_flux_pulid import PuLIDPipeline #type: ignore

        model_wrapper = model.model.diffusion_model
        if not isinstance(model_wrapper, ComfyFluxWrapper):
            raise TypeError(
                f"Expected ComfyFluxWrapper, got {type(model_wrapper).__name__}.\n"
                "This node only works with models loaded via Eclipse's Smart Loader Plus (Nunchaku mode)."
            )
        transformer = model_wrapper.model

        device = comfy.model_management.get_torch_device()
        weight_dtype = next(transformer.parameters()).dtype

        pulid_path = get_full_path_or_raise("pulid", pulid_file)
        eva_clip_path = get_full_path_or_raise("clip", eva_clip_file)
        insightface_dirpath = folder_paths.get_folder_paths("insightface")[0]
        facexlib_dirpath = folder_paths.get_folder_paths("facexlib")[0]

        # Map user-facing provider to nunchaku's expected value
        onnx_provider = _PROVIDER_MAP.get(insight_face_provider, "gpu")

        pulid_pipeline = PuLIDPipeline(
            dit=transformer,
            device=device,
            weight_dtype=weight_dtype,
            onnx_provider=onnx_provider,
            pulid_path=pulid_path,
            eva_clip_path=eva_clip_path,
            insightface_dirpath=insightface_dirpath,
            facexlib_dirpath=facexlib_dirpath,
        )

        log.msg(_LOG_PREFIX, f"✓ PuLID pipeline loaded (provider={insight_face_provider})")
        return io.NodeOutput(model, pulid_pipeline)


class RvTools_NunchakuPuLIDApply(io.ComfyNode):
    # Nunchaku PuLID Apply — applies PuLID identity to a nunchaku FLUX model.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Nunchaku PuLID Apply [Eclipse]",
            display_name="Nunchaku PuLID Apply",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            description="Apply PuLID identity embedding to a Nunchaku FLUX model (Eclipse version).",
            inputs=[
                io.Model.Input("model", tooltip="The nunchaku FLUX model."),
                io.Custom("PULID_PIPELINE").Input("pulid_pipeline",
                    tooltip="PuLID pipeline from the Nunchaku PuLID Loader."),
                io.Image.Input("image", tooltip="Reference face image(s)."),
                io.Float.Input(
                    "weight",
                    default=1.0,
                    min=-1.0,
                    max=5.0,
                    step=0.05,
                    tooltip="Strength of the identity guidance.",
                ),
                io.Float.Input(
                    "start_at",
                    default=0.0,
                    min=0.0,
                    max=1.0,
                    step=0.001,
                    tooltip="Starting timestep for applying PuLID.",
                ),
                io.Float.Input(
                    "end_at",
                    default=1.0,
                    min=0.0,
                    max=1.0,
                    step=0.001,
                    tooltip="Ending timestep for applying PuLID.",
                ),
            ],
            outputs=[
                io.Model.Output("model"),
            ],
        )

    @classmethod
    def execute(cls, model, pulid_pipeline, image, weight, start_at, end_at):
        from nunchaku.models.pulid.pulid_forward import pulid_forward #type: ignore

        # Extract ID embeddings from all face images
        all_embeddings = []
        for i in range(image.shape[0]):
            single_image = image[i:i + 1].squeeze().cpu().numpy() * 255.0
            single_image = np.clip(single_image, 0, 255).astype(np.uint8)

            id_embedding, _ = pulid_pipeline.get_id_embedding(single_image)
            if id_embedding is not None:
                all_embeddings.append(id_embedding)

        if not all_embeddings:
            log.warning(_LOG_PREFIX, "No face detected in any of the images. Skipping PuLID.")
            return io.NodeOutput(model)

        id_embeddings = torch.mean(torch.stack(all_embeddings), dim=0)

        model_wrapper = model.model.diffusion_model
        if not isinstance(model_wrapper, ComfyFluxWrapper):
            raise TypeError(
                f"Expected ComfyFluxWrapper, got {type(model_wrapper).__name__}.\n"
                "This node only works with models loaded via Eclipse's Smart Loader Plus (Nunchaku mode)."
            )

        ret_model_wrapper, ret_model = copy_with_ctx(model_wrapper)

        ret_model_wrapper.pulid_pipeline = pulid_pipeline
        ret_model_wrapper.customized_forward = partial(
            pulid_forward,
            id_embeddings=id_embeddings,
            id_weight=weight,
            start_timestep=start_at,
            end_timestep=end_at,
        )

        log.msg(_LOG_PREFIX, f"✓ PuLID applied (weight={weight}, range={start_at}-{end_at})")
        return io.NodeOutput(ret_model)
