from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvText_DualText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="String Dual [Eclipse]",
            display_name="String Dual",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=[
                io.String.Input("txt_pos", multiline=True, default=""),
                io.String.Input("txt_neg", multiline=True, default=""),
            ],
            outputs=[
                io.String.Output("txt_pos"),
                io.String.Output("txt_neg"),
            ],
        )

    @classmethod
    def execute(cls, txt_pos, txt_neg):
        txt_pos = txt_pos.strip()
        txt_neg = txt_neg.strip()
        return io.NodeOutput(txt_pos, txt_neg)