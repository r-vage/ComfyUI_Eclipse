# Basic Pipe [Eclipse]
# Combined node to pack, unpack, and edit standard ComfyUI BASIC_PIPE.

from comfy_api.latest import io  # type: ignore
from ...core import CATEGORY

class RvPipe_BasicPipe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Basic Pipe [Eclipse]",
            display_name="⚠ Basic Pipe",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            inputs=[
                io.Custom("BASIC_PIPE").Input("basic_pipe", optional=True, tooltip="Optional basic pipe tuple to unpack or edit."),
                io.Model.Input("model", optional=True, tooltip="Optional model override/input."),
                io.Clip.Input("clip", optional=True, tooltip="Optional CLIP override/input."),
                io.Vae.Input("vae", optional=True, tooltip="Optional VAE override/input."),
                io.Conditioning.Input("positive", optional=True, tooltip="Optional positive conditioning override/input."),
                io.Conditioning.Input("negative", optional=True, tooltip="Optional negative conditioning override/input."),
            ],
            outputs=[
                io.Custom("BASIC_PIPE").Output("basic_pipe", tooltip="The resolved basic pipe tuple."),
                io.Model.Output("model", tooltip="The resolved model."),
                io.Clip.Output("clip", tooltip="The resolved CLIP model."),
                io.Vae.Output("vae", tooltip="The resolved VAE model."),
                io.Conditioning.Output("positive", tooltip="The resolved positive conditioning."),
                io.Conditioning.Output("negative", tooltip="The resolved negative conditioning."),
            ],
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
        )

    @classmethod
    def execute(cls, basic_pipe=None, model=None, clip=None, vae=None, positive=None, negative=None):
        if basic_pipe is not None:
            if not isinstance(basic_pipe, (list, tuple)) or len(basic_pipe) < 5:
                raise ValueError("Basic Pipe [Eclipse]: The input 'basic_pipe' is invalid or not fully packed. "
                                 "Please connect a valid BASIC_PIPE tuple (model, clip, vae, positive, negative).")
            pipe_model, pipe_clip, pipe_vae, pipe_positive, pipe_negative = basic_pipe[:5]
        else:
            pipe_model, pipe_clip, pipe_vae, pipe_positive, pipe_negative = None, None, None, None, None

        # Resolve each component with overrides
        final_model = model if model is not None else pipe_model
        final_clip = clip if clip is not None else pipe_clip
        final_vae = vae if vae is not None else pipe_vae
        final_positive = positive if positive is not None else pipe_positive
        final_negative = negative if negative is not None else pipe_negative

        # Check for missing elements
        missing = []
        if final_model is None: missing.append("model")
        if final_clip is None: missing.append("clip")
        if final_vae is None: missing.append("vae")
        if final_positive is None: missing.append("positive")
        if final_negative is None: missing.append("negative")

        if missing:
            raise ValueError(f"Basic Pipe [Eclipse]: Missing required basic pipe elements: {', '.join(missing)}. "
                             f"Ensure either 'basic_pipe' is connected, or the individual optional inputs are connected.")

        new_pipe = (final_model, final_clip, final_vae, final_positive, final_negative)
        return io.NodeOutput(new_pipe, final_model, final_clip, final_vae, final_positive, final_negative)
