from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvPipe_Out_WanVideo_Setup(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Pipe Out WanVideo Setup [Eclipse]",
            display_name="Pipe Out WanVideo Setup",
            category=CATEGORY.MAIN.value + CATEGORY.PIPE.value,
            inputs=[
                io.Custom("PIPE").Input("pipe", tooltip="Input dict-style pipe containing steps, cfg, model_shift, steps_start, and steps_stop."),
            ],
            outputs=[
                io.Int.Output("steps"),
                io.Float.Output("cfg"),
                io.Float.Output("model_shift"),
                io.Int.Output("steps_start"),
                io.Int.Output("steps_stop"),
            ],
        )

    @classmethod
    def execute(cls, pipe=None):
        # Only accept dict-style pipes now.
        if pipe is None:
            raise ValueError("Input pipe must not be None and must be a dict-style pipe")
        if not isinstance(pipe, dict):
            raise ValueError("RvPipe_Out_WvW_Setup expects dict-style pipes only.")

        try:
            steps_val = pipe.get("steps")
            steps = int(steps_val) if steps_val is not None else 0
        except Exception:
            steps = 0
        try:
            cfg_val = pipe.get("cfg")
            cfg = float(cfg_val) if cfg_val is not None else 0.0
        except Exception:
            cfg = 0.0
        try:
            model_shift_val = pipe.get("model_shift")
            model_shift = float(model_shift_val) if model_shift_val is not None else 0.0
        except Exception:
            model_shift = 0.0
        try:
            steps_start_val = pipe.get("steps_start")
            steps_start = int(steps_start_val) if steps_start_val is not None else 0
        except Exception:
            steps_start = 0
        try:
            steps_stop_val = pipe.get("steps_stop")
            steps_stop = int(steps_stop_val) if steps_stop_val is not None else 0
        except Exception:
            steps_stop = 0

        return io.NodeOutput(steps, cfg, model_shift, steps_start, steps_stop)