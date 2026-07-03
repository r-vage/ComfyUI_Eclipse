import math
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "RIFE Multiplier"


class RvConversion_RIFEMultiplier(io.ComfyNode):
    # Calculates the integer multiplier needed to reach a target FPS from a source FPS.
    # The result can be fed directly to a RIFE interpolation node.
    #
    # Formula: multiplier = max(1, round(target_fps / source_fps))
    # actual_fps = source_fps * multiplier   (may differ slightly from target)

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="RIFE Multiplier [Eclipse]",
            display_name="RIFE Multiplier",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            description="Calculates the RIFE interpolation multiplier needed to reach a target FPS from a given source FPS. Outputs the nearest integer multiplier ≥ 1 and the actual FPS that results from applying it.",
            inputs=[
                io.Float.Input("source_fps", default=24.0, min=1.0, max=960.0, step=0.001,
                    tooltip="FPS of the input video or image sequence. For image batches, set this to the intended playback rate of your frames."),
                io.Float.Input("target_fps", default=60.0, min=1.0, max=960.0, step=0.001,
                    tooltip="Desired output FPS after RIFE interpolation."),
            ],
            outputs=[
                io.Int.Output("multiplier",
                    tooltip="Nearest integer multiplier to pass to the RIFE node. Always ≥ 1."),
                io.Float.Output("actual_fps",
                    tooltip="Actual FPS achieved after applying the multiplier (source_fps × multiplier). May differ slightly from target_fps."),
            ],
        )

    @classmethod
    def execute(cls, source_fps: float, target_fps: float):
        if source_fps <= 0.0:
            log.error(_LOG_PREFIX, f"source_fps must be > 0, got {source_fps}. Defaulting to 1.")
            source_fps = 1.0

        exact = target_fps / source_fps
        multiplier = max(1, round(exact))
        actual_fps = source_fps * multiplier

        log.debug(_LOG_PREFIX,
            f"{source_fps} fps → target {target_fps} fps | exact={exact:.3f}x → multiplier={multiplier}x → actual={actual_fps:.3f} fps")

        return io.NodeOutput(multiplier, actual_fps)
