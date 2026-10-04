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


def _single_setting(value, name):
    # List input mode also wraps widgets. Never discard extra control values.
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError(f"{name} requires one value; Shot Planner creates one plan per execution.")
        return value[0]
    return value


def _combined_text(value, name):
    if isinstance(value, list):
        if any(not isinstance(part, str) for part in value):
            raise TypeError(f"{name} must contain only text.")
        return "\n\n".join(part for part in value if part.strip())
    return value


class RvText_CharacterShotPlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Character Shot Planner [Eclipse]",
            display_name="Character Shot Planner",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            description="Creates one plan per execution, accepting the whole input list at once. Planner creates close, mid and wide candidates and selects one per image. Manual accepts multiline text or a STRING list, with shared character, outfit and scene text added to each shot, always as a preview without planner files or reservation history. Text lists on shared fields are combined; other settings require one value.",
            is_input_list=True,
            not_idempotent=True,
            inputs=[
                io.String.Input("project", default="my-character", tooltip="Planner: name of the persistent history under output/eclipse/shot_ledgers. All batches with this name share reservations. Reset reservations starts fresh; changing batch_id or reloading files does not. The report shows the ledger path. Manual: a label used to derive stable per-entry seeds; no history is accessed."),
                io.String.Input("batch_id", default="batch-001", tooltip="Planner: same ID and settings replay saved shots; changed settings use -2, -3, etc. A new ID requests fresh shots, not a history reset. Pool edits need a new ID. Manual: labels shot IDs and contributes to seeds. Keep it unchanged across manual_start batches to preserve per-entry results."),
                io.Int.Input("count", default=40, min=1, max=100, tooltip="Maximum selected prompts in this batch (1–100), not completed images. Manual takes up to count nonempty entries from manual_start without wrapping. Planner creates three candidates per shot; coverage completion can return fewer. Reserve stops automatic queueing on coverage completion; preview shows capacity information. Try 3 while testing."),
                io.String.Input("character_lock", multiline=True, default="The same character as the reference images; preserve facial identity, hair and body proportions.", tooltip="Shared identity and reference-role instructions included in every shot. Connect Character Reference Pack's character_lock here. A text list is joined with blank lines into one shared lock, not mapped to separate plans. Required in Planner; keep fixed pose/framing instructions out so the planner can vary them. Optional in Manual."),
                io.String.Input("outfit_lock", multiline=True, default="", tooltip="Optional wardrobe instructions added to every shot. A text list is joined with blank lines into one shared outfit lock. Leave empty if character_lock already specifies the outfit. Keep consistent with clothing references and the approved anchor."),
                io.String.Input("scene", multiline=True, default="An uninterrupted featureless white background and light floor, soft natural light.", tooltip="Shared background and lighting text added to every shot in both modes. Text lists are joined with blank lines into one shared scene. Planner gets camera, pose and expression from its pools; Manual uses only the supplied entry and shared fields. This does not change the separate character-review scene."),
                io.Combo.Input("body_mode", options=["any", *BODY_MODES], default="any", tooltip="Posture filter: any mixes seated, standing and lying poses. Seated includes benches, stools, floor cushions and supported reclining; lying keeps the body horizontal on a support. Standing + sports includes boxing and martial arts. Close portraits retain posture but cannot show full-body actions. Filters need compatible poses in your editable files; high cooldowns can exhaust small pools."),
                io.Combo.Input("selection", options=["balanced", "close", "mid", "wide"], default="balanced", tooltip="Which candidate becomes the output image prompt. Balanced favors the least-used distance across project history; close/mid/wide always select that distance. All three candidates are still planned and reserved. Nonempty choices overrides this setting."),
                io.String.Input("choices", multiline=True, default="", tooltip="Optional manual selection, one close/mid/wide per requested shot, separated by commas or newlines. A connected text list is combined in order. Example for count=3: close, wide, mid. Overrides selection. Choices affect later cooldowns, so preview again after editing."),
                io.Int.Input("seed", default=0, min=0, max=2**64 - 1, control_after_generate=False, tooltip="Controls candidate draws and per-shot generation seeds. A connection overrides this widget. Keep fixed for retries; changing it creates a variant in Planner reserve mode. Manual derives seeds from this value, project, batch_id and absolute entry position, so changing manual_start/count preserves each entry's seed. Does not control reference/anchor sampler seeds."),
                io.Int.Input("pose_cooldown", default=3, min=0, max=20, tooltip="Number of previous selected shots whose poses cannot be offered again, including earlier batches. 0 allows immediate reuse between sets; the three candidates within a set still have distinct poses. Lower this if a restricted pose pool runs out."),
                io.Int.Input("expression_cooldown", default=3, min=0, max=20, tooltip="Applies only to expression=random: number of previous selected shots whose expressions cannot be offered again, including earlier batches. 0 allows immediate reuse between sets; random candidates within a set still have distinct expressions. Fixed expressions and off ignore this cooldown."),
                io.Int.Input("camera_cooldown", default=2, min=0, max=20, tooltip="Number of previous selected shots whose camera families are excluded, including earlier batches. In cameras stop mode, exact combinations never repeat even at 0. Other stop modes cycle depleted camera pools and relax this cooldown if necessary, with a warning, so cameras do not end pose/expression coverage."),
                io.Combo.Input("operation", options=["reserve", "preview"], default="reserve", tooltip="preview: plan from empty history using current files, ignoring all saved reservations and batches; no history is saved and downstream generation remains enabled. reserve: save all three candidates per shot before generation, including skipped ones. Later generation failure does not undo reservations; retry unchanged settings to replay. Planning failure saves none of the batch. Existing batches replay only in reserve mode. Preview and reserve match when project history is empty."),
                # Keep socket order stable; the frontend places these controls after body_mode.
                io.Combo.Input("pose_category", options=["all", *POSE_CATEGORIES], default="all", optional=True, tooltip="Activity filter, combined with body_mode. all includes everyday, sports and sexy. Everyday selects casual poses; sports selects solo boxing, martial arts, warm-ups and exercise; sexy selects suggestive solo glamour poses for standing, seated and lying subjects. Does not change wardrobe, facial expression or location. Edit category on poses in prompts/shot_planner/poses.json; omitted category means everyday. Existing reserved batches replay their saved prompts."),
                io.Combo.Input("expression", options=expression_options(), default="random", optional=True, tooltip="random draws distinct expressions with cooldowns. A specific ID applies that expression to every candidate and ignores expression_cooldown. off omits planner expression wording while preserving your prompt text. Edit expressions.json and click Reload planner files to refresh choices. Removed IDs fail for new batches; reserved batches replay their snapshots."),
                io.Combo.Input("stop_when", options=list(STOP_MODES), default="poses", optional=True, tooltip="poses (default): finish when every compatible pose has appeared in a selected output. expressions: cover each selected expression; fixed/off has one variant. poses_and_expressions: finish both lists, cycling the shorter one. cameras: legacy unique-camera limit. never: keep cycling. Camera pools cycle in all modes except cameras; camera cooldown may relax to continue. Reserve coverage uses project history; preview starts empty. Both use current body/category/output-distance filters. The final coverage batch can be shorter than count. Reserve shows a yellow popup and stops automatic queueing; preview shows capacity information. Reserved batches replay unchanged in reserve mode."),
                # Append sockets to preserve saved links; JS places widgets below batch_id.
                io.Combo.Input("mode", options=["Planner", "Manual"], default="Planner", optional=True, tooltip="Planner uses the editable pools and ignores manual_shots. Manual uses each trimmed nonempty line from manual_shots, in order with duplicates, plus shared character/outfit/scene text. Manual always previews without reading or saving reservations. Switching modes preserves hidden values and connections."),
                io.Int.Input("manual_start", default=1, min=1, max=2**31 - 1, optional=True, tooltip="Manual only: 1-based first nonempty entry. With count=25 use starts 1 and 26 for consecutive batches. Takes up to count entries without wrapping; a start beyond the list is an error. Absolute entry numbers keep IDs and seeds stable across batches when project, batch_id and seed stay unchanged."),
                io.String.Input("manual_shots", default="", optional=True, force_input=True, tooltip="Connect multiline text or a STRING list, including Wildcard Processor List's list output. The whole list is consumed once; each trimmed nonempty line supplies one shot. Order and duplicates are preserved. Shared character, outfit and scene text is added to every shot. Required in Manual, ignored in Planner."),
            ],
            outputs=[
                io.Custom("ECLIPSE_SHOT_PLAN").Output("plan", tooltip="Complete plan with settings and selected shots: three candidates per Planner shot, one per Manual entry. Connect to Shot Plan Slice to split generation into ranges. Manual plans never contain reservations."),
                io.String.Output("prompts", is_output_list=True, tooltip="One selected positive prompt per shot, in order. Connect to the dataset text encoder. Keep aligned with seeds and shot_ids."),
                io.Int.Output("seeds", is_output_list=True, tooltip="One generation seed per selected prompt. Connect to the dataset sampler's seed input; do not reuse the planner seed as a substitute."),
                io.String.Output("shot_ids", is_output_list=True, tooltip="Stable IDs: batch ID / shot number / distance, or batch ID / absolute entry number / manual. Aligned with prompts and seeds; useful for filenames or captions."),
                io.String.Output("report", tooltip="Readable plan with prompts and seeds. Planner reports the ledger and all candidates; Manual reports the absolute entry range. Connect to Show Any to inspect before generating."),
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
                camera_cooldown, operation, pose_category="all", expression="random", stop_when="cameras",
                mode="Planner", manual_start=1, manual_shots=None):
        mode = _single_setting(mode, "mode")
        if mode not in ("Planner", "Manual"):
            raise ValueError("Mode must be Planner or Manual.")
        settings = {
            name: _single_setting(value, name) for name, value in {
                "project": project, "batch_id": batch_id, "count": count, "seed": seed,
            }.items()
        }
        settings.update({
            name: _combined_text(value, name) for name, value in {
                "character_lock": character_lock, "outfit_lock": outfit_lock, "scene": scene,
            }.items()
        })
        if mode == "Manual":
            settings["manual_start"] = _single_setting(manual_start, "manual_start")
            path = None
        else:
            settings.update({
                name: _single_setting(value, name) for name, value in {
                    "body_mode": body_mode, "selection": selection, "pose_cooldown": pose_cooldown,
                    "expression_cooldown": expression_cooldown, "camera_cooldown": camera_cooldown,
                    "operation": operation, "pose_category": pose_category, "expression": expression,
                    "stop_when": stop_when,
                }.items()
            })
            settings["choices"] = _combined_text(choices, "choices")
            path = ledger_path(folder_paths.get_output_directory(), settings["project"])
        try:
            plan = plan_shots(path, **settings, mode=mode, manual_shots=manual_shots)
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
                                 "eclipse_shot_planner_stopped": [plan["operation"] == "reserve" and bool(plan.get("exhausted"))]})


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
                io.Int.Input("start", default=0, min=0, max=99, tooltip="Zero-based position within this plan: 0 starts at its first shot, even when manual_start is 26. Must be inside ordinary Planner plans; Manual and final coverage plans skip branches beyond the end."),
                io.Int.Input("count", default=0, min=0, max=100, tooltip="Number of selected shots; 0 means all remaining. For a 50-shot plan, start=25/count=25 returns shots 26–50. Manual and final coverage plans truncate to remaining shots or skip empty branches. Other Planner ranges must fit the plan. IDs, seeds and reservations stay unchanged."),
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
