from __future__ import annotations

# VAE Loader [Eclipse] — Standalone VAE loader with name passthrough
#
# Loads a VAE model via the upstream comfy.sd.VAE constructor (mirrors
# the stock VAELoader node) and outputs both the VAE and its filename.

import folder_paths  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.model_loader_common import load_custom_vae
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "VAE Loader"


class RvLoader_VaeLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        vaes = folder_paths.get_filename_list("vae")

        return io.Schema(
            node_id="VAE Loader [Eclipse]",
            display_name="VAE Loader",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Load a VAE model (with enhanced Wan 2.1 support) and output both the VAE and its filename.",
            inputs=[
                io.Combo.Input("vae_name", options=vaes, tooltip="VAE model to load"),
                io.Boolean.Input("disable_offload", default=True, tooltip="Keep VAE on GPU (disable offloading)"),
            ],
            outputs=[
                io.Vae.Output("vae"),
                io.String.Output("vae_name"),
            ],
        )

    @classmethod
    def execute(cls, vae_name, disable_offload=True):
        vae = load_custom_vae(vae_name, disable_offload=disable_offload)
        log.msg(_LOG_PREFIX, f"Loaded: {vae_name}")
        return io.NodeOutput(vae, vae_name)
