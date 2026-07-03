from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvCond_CLIPTextEncode(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CLIP Text Encode [Eclipse]",
            display_name="CLIP Text Encode",
            category=CATEGORY.MAIN.value + CATEGORY.CONDITIONING.value,
            description="Encodes a text prompt using a CLIP model. Text input is a forced connection (no widget).",
            inputs=[
                io.Clip.Input("clip", tooltip="The CLIP model used for encoding the text."),
                io.String.Input("text", force_input=True, tooltip="The text to be encoded."),
            ],
            outputs=[
                io.Conditioning.Output("conditioning", tooltip="A conditioning containing the embedded text used to guide the diffusion model."),
            ],
        )

    @classmethod
    def execute(cls, clip, text):
        if clip is None:
            raise RuntimeError("ERROR: clip input is invalid: None\n\nIf the clip is from a checkpoint loader node your checkpoint does not contain a valid clip or text encoder model.")
        tokens = clip.tokenize(text)
        return io.NodeOutput(clip.encode_from_tokens_scheduled(tokens))
