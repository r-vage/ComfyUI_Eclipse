# Integrated model selection with bounded VIDEO input and output.

from comfy_api.latest import io

from ..core import CATEGORY
from ..core.node_debug import debug_node
from ..core.video_upscale import (
    NO_MODELS,
    RESIZE_MODES,
    model_files,
    model_fingerprint,
    upscale_video,
)


class RvVideo_Upscale(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        models = list(model_files()) or [NO_MODELS]
        return io.Schema(
            node_id="Upscale Video [Eclipse]", display_name="Upscale Video",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Stream VIDEO through an integrated upscale model one batch at a time. PyTorch uses ComfyUI's configured device or CPU; optional TensorRT engines use NVIDIA CUDA. Returns VIDEO with source timing and audio, ready for Save Video with Generation Data.",
            inputs=[
                io.Video.Input("video"),
                io.Combo.Input("model_name", options=models, tooltip="PyTorch models: models/upscale_models. Prebuilt TensorRT engines: models/tensorrt/upscaler. The selected file determines the backend; no extra custom-node pack is required."),
                io.Int.Input("batch_size", default=2, min=1, max=64, tooltip="Maximum frames decoded and upscaled together. Lower values reduce RAM/VRAM. TensorRT must support this batch size and input resolution."),
                io.Combo.Input("resize_to", options=RESIZE_MODES, default="model", tooltip="Model uses the model's native scale. Other sizes resize its output. 1080p/2K/4K preserve aspect ratio using a 1920/2560/3840-pixel long edge."),
                io.Int.Input("resize_width", default=1920, min=1, max=16384, tooltip="Used only for custom output size."),
                io.Int.Input("resize_height", default=1080, min=1, max=16384, tooltip="Used only for custom output size."),
                io.Combo.Input("device", options=["auto", "cpu"], default="auto", tooltip="Auto uses ComfyUI's configured PyTorch device, including supported AMD/Apple/Intel devices. CPU works with PyTorch models. TensorRT requires NVIDIA CUDA."),
                io.Int.Input("tile_size", default=512, min=64, max=2048, step=64, tooltip="PyTorch spatial tile size. Smaller tiles reduce inference memory; overlap blends edges. TensorRT uses its compiled profile instead."),
            ],
            outputs=[io.Video.Output("video"), io.String.Output("report")],
        )

    @classmethod
    def fingerprint_inputs(cls, model_name, **kwargs):
        return model_fingerprint(model_name)

    @classmethod
    @debug_node("UpscaleVideo", values=("resize_to", "device"))
    def execute(cls, video, model_name, batch_size=2, resize_to="model", resize_width=1920,
                resize_height=1080, device="auto", tile_size=512):
        return io.NodeOutput(*upscale_video(video, model_name, batch_size, resize_to, resize_width,
                                           resize_height, device, tile_size))
