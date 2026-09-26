# Decode one scene with native VAE semantics; expose only exact disk-backed frames.
from comfy_api.latest import io

from ..core import CATEGORY
from ..core.frame_timeline import TIMELINE_TYPE, FrameTimeline, append_frames


class RvVideo_DecodeAppendTimeline(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Decode and Append Timeline [Eclipse]",
            display_name="Decode and Append Timeline",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Native VAE decode, retain an exact frame range and append lossless temporary NumPy pixels. Outputs contain no image tensors. Files live as long as cached timelines reference them.",
            inputs=[io.Latent.Input("samples"), io.Vae.Input("vae"),
                    io.Int.Input("crop_start", default=0, min=0),
                    io.Int.Input("keep_frames", default=1, min=1, max=1000000),
                    io.Float.Input("fps", default=24, min=1, max=120),
                    io.Custom(TIMELINE_TYPE).Input("timeline", optional=True)],
            outputs=[io.Custom(TIMELINE_TYPE).Output("timeline")],
        )

    @classmethod
    def execute(cls, samples, vae, crop_start, keep_frames, fps, timeline=None):
        latent = samples["samples"]
        if latent.is_nested:
            latent = latent.unbind()[0]
        images = vae.decode(latent)
        if images.ndim == 5:
            images = images.reshape(-1, *images.shape[-3:])
        if crop_start < 0 or keep_frames < 1 or crop_start + keep_frames > images.shape[0]:
            raise ValueError("Planned retained range exceeds the decoded scene.")
        return io.NodeOutput(append_frames(images[crop_start:crop_start + keep_frames], fps, timeline))


class RvVideo_TrimTimeline(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Trim Frame Timeline [Eclipse]",
            display_name="Trim Frame Timeline",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            inputs=[io.Custom(TIMELINE_TYPE).Input("timeline"),
                    io.Int.Input("start", default=0, min=0),
                    io.Int.Input("count", default=1, min=1, max=1000000)],
            outputs=[io.Custom(TIMELINE_TYPE).Output("timeline"),
                     io.Int.Output("width"), io.Int.Output("height"), io.Int.Output("count")],
        )

    @classmethod
    def execute(cls, timeline, start, count):
        if not isinstance(timeline, FrameTimeline):
            raise TypeError("Expected an Eclipse frame timeline.")
        result = timeline.slice(start, count)
        return io.NodeOutput(result, result.width, result.height, len(result))


class RvVideo_TimelineLoopGate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Frame Timeline Loop Gate [Eclipse]",
            display_name="Frame Timeline Loop Gate",
            category=CATEGORY.MAIN.value + CATEGORY.VIDEO.value,
            description="Block extension execution when there are no extensions. Use has_extensions with a lazy If/Else to choose loop output or the base timeline.",
            inputs=[io.Custom(TIMELINE_TYPE).Input("timeline"),
                    io.Int.Input("extensions", default=0, min=0, max=1000000)],
            outputs=[io.Custom(TIMELINE_TYPE).Output("loop_timeline"),
                     io.Boolean.Output("has_extensions")],
        )

    @classmethod
    def execute(cls, timeline, extensions):
        from comfy_execution.graph_utils import ExecutionBlocker

        return io.NodeOutput(timeline if extensions > 0 else ExecutionBlocker(None), extensions > 0)
