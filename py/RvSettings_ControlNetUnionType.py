from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

# From the model page: canny (0), tile (1), depth (2), blur (3), pose (4), gray (5), low quality (6)
# https://huggingface.co/Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro

UNION_CONTROLNET_TYPES = {
    "canny/lineart/anime_lineart/mlsd": 0,
    "tile": 1,
    "depth": 2,
    "blur": 3,
    "openpose": 4,
    "gray": 5,
    "low quality": 6,
}

class RvSettings_ControlNetUnionType(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ControlNet Set Union Types (Flux) [Eclipse]",
            display_name="ControlNet Set Union Types (Flux)",
            category=CATEGORY.MAIN.value + CATEGORY.SETTINGS.value,
            inputs=[
                io.ControlNet.Input("control_net", tooltip="ControlNet input object."),
                io.Combo.Input("type", options=list(UNION_CONTROLNET_TYPES.keys()), tooltip="Select the ControlNet union type."),
            ],
            outputs=[
                io.ControlNet.Output("control_net"),
            ],
        )

    @classmethod
    def execute(cls, control_net, type):
        # Sets the control_type extra argument for the ControlNet input.
        control_net = control_net.copy()
        type_number = UNION_CONTROLNET_TYPES.get(type, -1)
        if type_number >= 0:
            control_net.set_extra_arg("control_type", [type_number])
        else:
            control_net.set_extra_arg("control_type", [])
        return io.NodeOutput(control_net)