import folder_paths #type: ignore
from ..core import CATEGORY
from comfy_api.latest import io #type: ignore

class RvTools_LoraStack(io.ComfyNode):
    # A node to stack multiple LoRAs with weights and options.
    @classmethod
    def define_schema(cls):
        loras = ["None"] + folder_paths.get_filename_list("loras")

        inputs = [
            io.Combo.Input("mode", options=["standard", "model_only", "simple"], default="standard", socketless=True,
                           tooltip="standard: independent clip_weight per LoRA. model_only: LoRAs applied to model only (no CLIP). simple: clip_weight = model_weight."),
            io.Int.Input("lora_count", default=5, min=1, max=10, step=1, tooltip="Number of visible LoRA slots"),
        ]

        for i in range(1, 11):
            inputs.extend([
                io.Boolean.Input(f"switch_{i}", default=False, label_on="ON", label_off="OFF"),
                io.Combo.Input(f"lora_name_{i}", options=loras),
                io.Float.Input(f"model_weight_{i}", default=1.0, min=-10.0, max=10.0, step=0.01),
                io.Float.Input(f"clip_weight_{i}", default=1.0, min=-10.0, max=10.0, step=0.01),
            ])

        inputs.append(io.Custom("LORA_STACK").Input("lora_stack", optional=True))

        return io.Schema(
            node_id="Lora Stack [Eclipse]",
            display_name="Lora Stack",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            inputs=inputs,
            outputs=[
                io.Custom("LORA_STACK").Output("LORA_STACK"),
            ],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        # Accept **kwargs so ComfyUI skips built-in combo validation.
        # This prevents "Value not in list" errors for stale filenames
        # in saved workflows (e.g. LoRA files that were moved/deleted).
        # Actual file existence is validated at execution time.
        return True

    @classmethod
    def execute(cls, mode, lora_count,
                      lora_name_1, model_weight_1, clip_weight_1, switch_1, 
                      lora_name_2, model_weight_2, clip_weight_2, switch_2, 
                      lora_name_3, model_weight_3, clip_weight_3, switch_3, 
                      lora_name_4, model_weight_4, clip_weight_4, switch_4, 
                      lora_name_5, model_weight_5, clip_weight_5, switch_5,
                      lora_name_6, model_weight_6, clip_weight_6, switch_6,
                      lora_name_7, model_weight_7, clip_weight_7, switch_7,
                      lora_name_8, model_weight_8, clip_weight_8, switch_8,
                      lora_name_9, model_weight_9, clip_weight_9, switch_9,
                      lora_name_10, model_weight_10, clip_weight_10, switch_10,
                      lora_stack=None) -> io.NodeOutput:

        # Initialise the list
        lora_list=list()
        
        if lora_stack is not None:
            lora_list.extend([l for l in lora_stack if l[0] != "None"])
        
        # Determine clip weight based on mode:
        # model_only: clip_weight = None (signal to Apply to skip CLIP)
        # simple: clip_weight = model_weight (same weight for both)
        # standard: clip_weight = user-provided clip_weight
        def get_clip_weight(model_weight, clip_weight):
            if mode == "model_only":
                return None
            return model_weight if mode == "simple" else clip_weight
        
        if lora_name_1 != "None" and switch_1:
            lora_list.extend([(lora_name_1, model_weight_1, get_clip_weight(model_weight_1, clip_weight_1))])

        if lora_name_2 != "None" and switch_2:
            lora_list.extend([(lora_name_2, model_weight_2, get_clip_weight(model_weight_2, clip_weight_2))])

        if lora_name_3 != "None" and switch_3:
            lora_list.extend([(lora_name_3, model_weight_3, get_clip_weight(model_weight_3, clip_weight_3))])

        if lora_name_4 != "None" and switch_4:
            lora_list.extend([(lora_name_4, model_weight_4, get_clip_weight(model_weight_4, clip_weight_4))])

        if lora_name_5 != "None" and switch_5:
            lora_list.extend([(lora_name_5, model_weight_5, get_clip_weight(model_weight_5, clip_weight_5))])

        if lora_name_6 != "None" and switch_6:
            lora_list.extend([(lora_name_6, model_weight_6, get_clip_weight(model_weight_6, clip_weight_6))])

        if lora_name_7 != "None" and switch_7:
            lora_list.extend([(lora_name_7, model_weight_7, get_clip_weight(model_weight_7, clip_weight_7))])

        if lora_name_8 != "None" and switch_8:
            lora_list.extend([(lora_name_8, model_weight_8, get_clip_weight(model_weight_8, clip_weight_8))])

        if lora_name_9 != "None" and switch_9:
            lora_list.extend([(lora_name_9, model_weight_9, get_clip_weight(model_weight_9, clip_weight_9))])

        if lora_name_10 != "None" and switch_10:
            lora_list.extend([(lora_name_10, model_weight_10, get_clip_weight(model_weight_10, clip_weight_10))])

        return io.NodeOutput(lora_list)