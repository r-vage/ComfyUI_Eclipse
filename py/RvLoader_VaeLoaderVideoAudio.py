from __future__ import annotations

# VAE Loader Video+Audio [Eclipse] — Dual VAE loader for LTXV/LTX2
#
# Loads a video/image VAE and an LTXV/LTX2 audio VAE in one node, both from the
# vae folder, and outputs them on separate sockets. Intended for the GGUF LTX2
# flow where neither VAE is baked into the diffusion model file.

import folder_paths  # type: ignore

from ..core import CATEGORY
from ..core.logger import log
from ..core.model_loader_common import load_custom_vae
from comfy_api.latest import io  # type: ignore

_LOG_PREFIX = "VAE Loader Video+Audio"


class RvLoader_VaeLoaderVideoAudio(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        vaes = ["None"] + folder_paths.get_filename_list("vae")

        return io.Schema(
            node_id="VAE Loader Video+Audio [Eclipse]",
            display_name="VAE Loader Video+Audio",
            category=CATEGORY.MAIN.value + CATEGORY.LOADER.value,
            description="Load a video/image VAE and an LTXV/LTX2 audio VAE in one node "
                        "(both from the vae folder). Useful for GGUF LTX2 where neither "
                        "VAE is baked into the model file.",
            inputs=[
                io.Combo.Input("video_vae", options=vaes, default="None", tooltip="Video/image VAE file (vae folder). Set to None to skip."),
                io.Combo.Input("audio_vae", options=vaes, default="None", tooltip="LTXV/LTX2 audio VAE file (audio_vae./vocoder. weights) from the vae folder. Set to None to skip."),
                io.Boolean.Input("disable_offload", default=True, tooltip="Keep VAEs on GPU (disable offloading)."),
            ],
            outputs=[
                io.Vae.Output("video_vae"),
                io.Vae.Output("audio_vae"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        return True

    @classmethod
    def execute(cls, video_vae="None", audio_vae="None", disable_offload=True):
        loaded_video_vae = None
        loaded_audio_vae = None

        if video_vae not in (None, '', 'None'):
            try:
                loaded_video_vae = load_custom_vae(video_vae, disable_offload=disable_offload)
                log.msg(_LOG_PREFIX, f"Loaded video VAE: {video_vae}")
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Failed to load video VAE '{video_vae}': {e}")

        if audio_vae not in (None, '', 'None'):
            try:
                loaded_audio_vae = load_custom_vae(audio_vae, disable_offload=disable_offload)
                log.msg(_LOG_PREFIX, f"Loaded audio VAE: {audio_vae}")
            except Exception as e:
                log.warning(_LOG_PREFIX, f"Failed to load audio VAE '{audio_vae}': {e}")

        return io.NodeOutput(loaded_video_vae, loaded_audio_vae)
