# Character shot planning and aligned list outputs for generation branches.

import folder_paths  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.shot_planner import (
    STOP_MODES,
    ShotPoolExhausted,
    ledger_path,
    plan_report,
    plan_shots,
    selected_shots,
)
from ..core.shot_planner_pools import BODY_MODES, POSE_CATEGORIES, expression_options


class RvText_CharacterShotPlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Shot Planner [Eclipse]",
            display_name="Character Shot Planner",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description="Plan one close, mid and wide candidate per requested image, then select one. Project owns the history; batch ID names one saved plan. Reserve records all candidates before image generation, including skipped options; it does not track completed images. Use preview while testing, then reserve for final generation.",
            not_idempotent=True,
            inputs=[
                io.String.Input("project", default="my-character", tooltip="Name of the persistent history under output/eclipse/shot_ledgers. All batches with this project name share camera reservations. Use Reset reservations to start fresh with the same name. Changing batch_id or reloading prompt files does not clear history. The report shows the ledger file for manual backup/removal while the queue is stopped."),
                io.String.Input("batch_id", default="batch-001", tooltip="Names one plan within this project. Same ID and settings replay saved shots, useful when generation fails. Changed settings automatically use -2, -3, etc.; report shows the actual ID. A new ID requests fresh shots from the remaining pool, not a history reset. Pool-file edits need a new ID to take effect."),
                io.Int.Input("count", default=40, min=1, max=100, tooltip="Maximum selected prompts in this batch, not completed images. Each shot has three candidates recorded in reserve mode. Coverage completion can return fewer prompts. Reserve stops automatic queueing with a yellow popup; preview shows an informational capacity notice and continues. Try 3 while testing; this is not the project's lifetime limit."),
                io.String.Input("character_lock", multiline=True, default="The same character as the reference images; preserve facial identity, hair and body proportions.", tooltip="Identity and reference-role instructions included in every shot. Connect Character Reference Pack's character_lock here. Keep fixed pose/framing instructions out of this field so the planner can vary them. Changing it creates a new batch variant in reserve mode."),
                io.String.Input("outfit_lock", multiline=True, default="", tooltip="Optional wardrobe instructions added to every shot. Leave empty if the connected character_lock already specifies the outfit. Keep this consistent with clothing references and the approved anchor."),
                io.String.Input("scene", multiline=True, default="An uninterrupted featureless white background and light floor, soft natural light.", tooltip="Background and lighting for dataset shots. Applied to every planned prompt; camera, pose and expression come from the shot pools. This does not change the separate character-review scene."),
                io.Combo.Input("body_mode", options=["any", *BODY_MODES], default="any", tooltip="Posture filter: any mixes seated, standing and lying poses. Seated includes benches, stools, floor cushions and supported reclining; lying keeps the body horizontal on a support. Standing + sports includes boxing and martial arts. Close portraits retain posture but cannot show full-body actions. Filters need compatible poses in your editable files; high cooldowns can exhaust small pools."),
                io.Combo.Input("selection", options=["balanced", "close", "mid", "wide"], default="balanced", tooltip="Which candidate becomes the output image prompt. Balanced favors the least-used distance across project history; close/mid/wide always select that distance. All three candidates are still planned and reserved. Nonempty choices overrides this setting."),
                io.String.Input("choices", multiline=True, default="", tooltip="Optional manual selection, one close/mid/wide per requested shot, separated by commas or newlines. Example for count=3: close, wide, mid. Overrides selection. Choices affect later cooldowns, so preview again after editing."),
                io.Int.Input("seed", default=0, min=0, max=2**64 - 1, control_after_generate=False, tooltip="Controls new candidate draws and the per-shot generation seeds. A connected seed overrides this widget. Keep it fixed for retries; changing it requests a new batch variant in reserve mode. It does not reset reservations or control reference/anchor sampler seeds."),
                io.Int.Input("pose_cooldown", default=3, min=0, max=20, tooltip="Number of previous selected shots whose poses cannot be offered again, including earlier batches. 0 allows immediate reuse between sets; the three candidates within a set still have distinct poses. Lower this if a restricted pose pool runs out."),
                io.Int.Input("expression_cooldown", default=3, min=0, max=20, tooltip="Applies only to expression=random: number of previous selected shots whose expressions cannot be offered again, including earlier batches. 0 allows immediate reuse between sets; random candidates within a set still have distinct expressions. Fixed expressions and off ignore this cooldown."),
                io.Int.Input("camera_cooldown", default=2, min=0, max=20, tooltip="Number of previous selected shots whose camera families are excluded, including earlier batches. In cameras stop mode, exact combinations never repeat even at 0. Other stop modes cycle depleted camera pools and relax this cooldown if necessary, with a warning, so cameras do not end pose/expression coverage."),
                io.Combo.Input("operation", options=["reserve", "preview"], default="reserve", tooltip="preview: plan from empty history using current files, ignoring all saved reservations and batches; no history is saved and downstream generation remains enabled. reserve: save all three candidates per shot before generation, including skipped ones. Later generation failure does not undo reservations; retry unchanged settings to replay. Planning failure saves none of the batch. Existing batches replay only in reserve mode. Preview and reserve match when project history is empty."),
                # Keep socket order stable; the frontend places these controls after body_mode.
                io.Combo.Input("pose_category", options=["all", *POSE_CATEGORIES], default="all", optional=True, tooltip="Activity filter, combined with body_mode. all includes everyday, sports and sexy. Everyday selects casual poses; sports selects solo boxing, martial arts, warm-ups and exercise; sexy selects suggestive solo glamour poses for standing, seated and lying subjects. Does not change wardrobe, facial expression or location. Edit category on poses in prompts/shot_planner/poses.json; omitted category means everyday. Existing reserved batches replay their saved prompts."),
                io.Combo.Input("expression", options=expression_options(), default="random", optional=True, tooltip="random draws distinct expressions with cooldowns. A specific ID applies that expression to every candidate and ignores expression_cooldown. off omits planner expression wording while preserving your prompt text. Edit expressions.json and click Reload planner files to refresh choices. Removed IDs fail for new batches; reserved batches replay their snapshots."),
                io.Combo.Input("stop_when", options=list(STOP_MODES), default="poses", optional=True, tooltip="poses (default): finish when every compatible pose has appeared in a selected output. expressions: cover each selected expression; fixed/off has one variant. poses_and_expressions: finish both lists, cycling the shorter one. cameras: legacy unique-camera limit. never: keep cycling. Camera pools cycle in all modes except cameras; camera cooldown may relax to continue. Reserve coverage uses project history; preview starts empty. Both use current body/category/output-distance filters. The final coverage batch can be shorter than count. Reserve shows a yellow popup and stops automatic queueing; preview shows capacity information. Reserved batches replay unchanged in reserve mode."),
            ],
            outputs=[
                io.Custom("ECLIPSE_SHOT_PLAN").Output("plan", tooltip="Complete plan with all three candidates, selected choices and saved settings. Connect to Shot Plan Slice to split generation into ranges. This contains reservations, not image-completion records."),
                io.String.Output("prompts", is_output_list=True, tooltip="One selected positive prompt per shot, in order. Connect to the dataset text encoder. Keep aligned with seeds and shot_ids."),
                io.Int.Output("seeds", is_output_list=True, tooltip="One generation seed per selected prompt. Connect to the dataset sampler's seed input; do not reuse the planner seed as a substitute."),
                io.String.Output("shot_ids", is_output_list=True, tooltip="Stable IDs for selected shots: resolved batch ID / shot number / distance. Aligned with prompts and seeds; useful for filenames or captions."),
                io.String.Output("report", tooltip="Readable plan showing the resolved batch ID, ledger path and all candidates; * marks the selected candidate. Connect to Show Any to inspect it before generating images."),
            ],
        )

    @classmethod
    def validate_inputs(cls, expression="random"):
        # Bypass only the dynamic expression combo's current options. The planner
        # validates a new allocation against files, and replay against its snapshot.
        return True

    @classmethod
    def execute(cls, project, batch_id, count, character_lock, outfit_lock, scene,
                body_mode, selection, choices, seed, pose_cooldown, expression_cooldown,
                camera_cooldown, operation, pose_category="all", expression="random", stop_when="cameras"):
        path = ledger_path(folder_paths.get_output_directory(), project)
        try:
            plan = plan_shots(path, project=project, batch_id=batch_id, count=count,
                              character_lock=character_lock, outfit_lock=outfit_lock, scene=scene,
                              body_mode=body_mode, selection=selection, choices=choices, seed=seed,
                              pose_cooldown=pose_cooldown, expression_cooldown=expression_cooldown,
                              camera_cooldown=camera_cooldown, operation=operation,
                              pose_category=pose_category, expression=expression, stop_when=stop_when)
        except ShotPoolExhausted as error:
            from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore

            message = str(error)
            blocker = ExecutionBlocker(None)
            return io.NodeOutput(blocker, blocker, blocker, blocker, message,
                                 ui={"eclipse_shot_planner_notice": [message], "eclipse_shot_planner_stopped": [True]})
        shots = selected_shots(plan)
        return io.NodeOutput(plan, [s["prompt"] for s in shots], [s["seed"] for s in shots],
                             [s["shot_id"] for s in shots], plan_report(plan),
                             ui={"eclipse_shot_planner_notice": plan.get("warnings", []),
                                 "eclipse_shot_planner_info": plan.get("info", []),
                                 "eclipse_shot_planner_stopped": [operation == "reserve" and bool(plan.get("exhausted"))]})


class RvText_ShotPlanSlice(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Shot Plan Slice [Eclipse]",
            display_name="Shot Plan Slice",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description="Send a range of selected shots from an existing plan to a generation branch. Keeps prompts, seeds and IDs aligned; does not create or release reservations. Useful for splitting a batch or retrying a failed range.",
            inputs=[
                io.Custom("ECLIPSE_SHOT_PLAN").Input("plan", tooltip="Connect Character Shot Planner's plan output. The slice returns selected shots only, not the skipped close/mid/wide alternatives."),
                io.Int.Input("start", default=0, min=0, max=99, tooltip="Zero-based first selected shot: 0 starts at shot 1, 20 starts at shot 21. Must be inside ordinary plans; a branch beyond the end of a final coverage batch is skipped without error."),
                io.Int.Input("count", default=0, min=0, max=100, tooltip="Number of selected shots to return; 0 means all remaining shots. For a 50-shot plan, start=25/count=25 returns shots 26–50. The range must fit ordinary plans. Final coverage batches can be shorter: slices truncate to remaining shots or skip an empty branch. Earlier reservations remain intact."),
            ],
            outputs=[
                io.String.Output("prompts", is_output_list=True, tooltip="Selected prompts for this range, in their original order. Connect to this branch's text encoder."),
                io.Int.Output("seeds", is_output_list=True, tooltip="Matching generation seeds for this range. Connect to the same branch's sampler."),
                io.String.Output("shot_ids", is_output_list=True, tooltip="Original shot IDs for this range; slicing does not renumber shots. Aligned with the returned prompts and seeds."),
            ],
        )

    @classmethod
    def execute(cls, plan, start, count):
        shots = selected_shots(plan, start, count)
        if not shots:
            from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore

            blocker = ExecutionBlocker(None)
            return io.NodeOutput(blocker, blocker, blocker)
        return io.NodeOutput([s["prompt"] for s in shots], [s["seed"] for s in shots],
                             [s["shot_id"] for s in shots])
