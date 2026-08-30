from comfy_api.latest import io  # type: ignore
from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore

from ..core import CATEGORY
from ..core.danbooru_maintenance import (
    MAX_DANBOORU_SCORE,
    MAX_EXCLUDED_POST_TAG_LENGTH,
    MAX_EXCLUDED_POST_TAGS,
    MAX_POSTS_PER_RATING,
    MAX_REQUESTS_PER_RUN,
    begin_post_scan,
    finish_post_scan,
    maintenance_input_fingerprint,
    post_scan_stop_requested,
    run_maintenance,
)

ACTION_OPTIONS = (
    "refresh_ratings",
    "refresh_catalog",
    "prepare_ai",
    "manual_categorization",
    "resume",
)
RATING_OPTIONS = ("general", "sensitive", "questionable", "explicit")
DEFAULT_ACTIONS = "refresh_ratings,prepare_ai,resume"
DEFAULT_RATINGS = ",".join(RATING_OPTIONS)


def _selection_list(value: str | list[str]) -> list[str]:
    raw_values = value.split(",") if isinstance(value, str) else value
    return list(
        dict.fromkeys(
            item.strip()
            for item in raw_values
            if isinstance(item, str) and item.strip()
        )
    )


class _MaintenanceProgress:
    def __init__(self, node_id, page_input: str = "post_start_page") -> None:
        self._bar = None
        self._count = 0
        self._node_id = node_id
        self._page_input = page_input
        try:
            from comfy.utils import ProgressBar  # type: ignore
        except ImportError:
            return
        self._bar = ProgressBar(MAX_REQUESTS_PER_RUN)

    def update_request(self, count: int) -> None:
        self._count = count
        if self._bar is not None:
            self._bar.update_absolute(count, MAX_REQUESTS_PER_RUN)

    def _update_page(self, next_page: int, page_input: str) -> None:
        if self._node_id is None:
            return
        try:
            from server import PromptServer  # type: ignore

            PromptServer.instance.send_sync(
                "eclipse/danbooru_page_progress",
                {
                    "node_id": self._node_id,
                    "next_page": next_page,
                    "page_input": page_input,
                },
            )
        except (ImportError, AttributeError, OSError, RuntimeError):
            return

    def update_page(self, next_page: int) -> None:
        self._update_page(next_page, self._page_input)

    def update_tag_page(self, next_page: int) -> None:
        self._update_page(next_page, "tag_start_page")

    def finish_post_scan(self) -> None:
        if self._node_id is None:
            return
        try:
            from server import PromptServer  # type: ignore

            PromptServer.instance.send_sync(
                "eclipse/danbooru_scan_state",
                {"node_id": self._node_id, "active": False},
            )
        except (ImportError, AttributeError, OSError, RuntimeError):
            return

    def finish(self) -> None:
        if self._bar is not None:
            completed_total = max(self._count, 1)
            self._bar.update_absolute(completed_total, completed_total)


class RvText_DanbooruCorpusMaintenance(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Danbooru Corpus Maintenance [Eclipse]",
            display_name="Danbooru Corpus Maintenance",
            category=CATEGORY.MAIN.value + CATEGORY.DANBOORU.value,
            description=(
                "Atomically refresh Eclipse's Danbooru corpus, enrich the completed "
                "rating pools from the tag catalog, then prepare strict SmartLLM "
                "prompts or a provider-neutral manual categorization work package."
            ),
            inputs=[
                io.String.Input(
                    "actions",
                    default=DEFAULT_ACTIONS,
                    tooltip=(
                        "Maintenance phases selected by Eclipse's combined maintenance "
                        "and rating combo-chip bar."
                    ),
                ),
                io.String.Input(
                    "ratings",
                    default=DEFAULT_RATINGS,
                    tooltip=(
                        "Rating corpora selected by Eclipse's combined maintenance and "
                        "rating combo-chip bar."
                    ),
                ),
                io.Int.Input(
                    "post_start_page",
                    default=1,
                    min=1,
                    max=1000000000,
                    step=1,
                    tooltip=(
                        "First logical request for every selected rating. Resume uses "
                        "each rating's saved score band and request checkpoint; disable "
                        "resume to rewind only when the preceding checkpoint exists."
                    ),
                ),
                io.Int.Input(
                    "post_stop_page",
                    default=-1,
                    min=-1,
                    max=1000000000,
                    step=1,
                    tooltip=(
                        "Last logical post page to scan for each rating, inclusive. "
                        "Use -1 to continue until the rating target, score range end, "
                        "or the post-phase request window reserved ahead of catalog "
                        "enrichment."
                    ),
                ),
                io.Int.Input(
                    "target_per_rating",
                    default=MAX_POSTS_PER_RATING,
                    min=1,
                    max=MAX_POSTS_PER_RATING,
                    step=1,
                    tooltip=(
                        "Maximum number of unique posts retained for each selected "
                        "rating. Existing larger imported corpora are not truncated."
                    ),
                ),
                io.Combo.Input(
                    "score_range_mode",
                    options=["automatic", "custom"],
                    default="automatic",
                    tooltip=(
                        "Automatic samples scores 1024 through 5000 first, then "
                        "halves downward whenever a sample finds no unseen posts. Custom "
                        "samples one fixed inclusive range. Each request uses the "
                        "selected rating pool with the fewest retained posts."
                    ),
                ),
                io.Int.Input(
                    "custom_score_min",
                    default=0,
                    min=-1000000000,
                    max=MAX_DANBOORU_SCORE,
                    step=1,
                    tooltip=(
                        "Inclusive minimum Danbooru post score used in custom mode."
                    ),
                ),
                io.Int.Input(
                    "custom_score_max",
                    default=MAX_DANBOORU_SCORE,
                    min=-1000000000,
                    max=MAX_DANBOORU_SCORE,
                    step=1,
                    tooltip=(
                        "Inclusive maximum Danbooru post score used in custom mode."
                    ),
                ),
                io.Int.Input(
                    "minimum_tag_post_count",
                    default=100,
                    min=0,
                    max=1000000000,
                    step=1,
                    tooltip=(
                        "Before SmartLLM, inspect catalog tags with more posts than "
                        "this value and keep only tags present in the rating pools."
                    ),
                ),
                io.Int.Input(
                    "tag_start_page",
                    default=1,
                    min=1,
                    max=1000,
                    step=1,
                    tooltip=(
                        "First catalog-enrichment page. Resume restores the saved "
                        "page; disabling it permits a checkpointed rewind."
                    ),
                ),
                io.Int.Input(
                    "tag_stop_page",
                    default=-1,
                    min=-1,
                    max=1000,
                    step=1,
                    tooltip="Inclusive catalog stop page, or -1 for exhaustion.",
                ),
                io.Int.Input(
                    "maximum_tag_pages_per_queue",
                    default=100,
                    min=1,
                    max=1000,
                    step=1,
                    tooltip=(
                        "Catalog-page request budget reserved after post collection "
                        "and before SmartLLM preparation."
                    ),
                ),
                io.Int.Input(
                    "ai_batch_size",
                    default=100,
                    min=1,
                    max=500,
                    step=1,
                    tooltip=(
                        "General tags placed in each SmartLLM request or numbered "
                        "manual-categorization input file."
                    ),
                ),
                io.Int.Input(
                    "maximum_ai_batches",
                    default=1,
                    min=1,
                    max=32,
                    step=1,
                    tooltip=(
                        "Maximum mapped SmartLLM batches prepared per queue. Ignored "
                        "and hidden for manual categorization exports."
                    ),
                ),
                io.String.Input(
                    "excluded_post_tags",
                    default="",
                    multiline=True,
                    tooltip=(
                        "Deny newly returned posts containing any exact tag listed "
                        "here, separated by commas or new lines. Adjacent separators, "
                        "blank entries, and "
                        f"duplicate entries are ignored; at most {MAX_EXCLUDED_POST_TAGS} tags of "
                        f"{MAX_EXCLUDED_POST_TAG_LENGTH} characters each are allowed."
                    ),
                ),
            ],
            hidden=[io.Hidden.unique_id],
            outputs=[
                io.String.Output(
                    "categorization_system_prompt",
                    is_output_list=True,
                    tooltip="Connect to Smart LM Loader #1 system_prompt.",
                ),
                io.String.Output(
                    "categorization_prompt",
                    is_output_list=True,
                    tooltip="Connect to Smart LM Loader #1 user_prompt.",
                ),
                io.String.Output(
                    "review_system_prompt",
                    is_output_list=True,
                    tooltip="Connect to Smart LM Loader #2 system_prompt.",
                ),
                io.String.Output(
                    "batch_token",
                    is_output_list=True,
                    tooltip="Connect to Danbooru Category Apply batch_token.",
                ),
                io.String.Output("report", tooltip="Refresh and pending-work report."),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(
        cls,
        actions: str | list[str] = DEFAULT_ACTIONS,
        **_kwargs,
    ) -> float | tuple:
        return maintenance_input_fingerprint(_selection_list(actions))

    @classmethod
    def execute(
        cls,
        actions: str | list[str],
        ratings: str | list[str],
        post_start_page: int,
        post_stop_page: int,
        target_per_rating: int,
        score_range_mode: str,
        custom_score_min: int,
        custom_score_max: int,
        minimum_tag_post_count: int,
        tag_start_page: int,
        tag_stop_page: int,
        maximum_tag_pages_per_queue: int,
        ai_batch_size: int,
        maximum_ai_batches: int,
        excluded_post_tags: str,
    ) -> io.NodeOutput:
        node_id = cls.hidden.unique_id
        if isinstance(node_id, list):
            node_id = node_id[0] if node_id else None
        selected_actions = _selection_list(actions)
        control_id = (
            begin_post_scan(node_id)
            if node_id is not None and "refresh_ratings" in selected_actions
            else None
        )
        progress = _MaintenanceProgress(node_id)

        def finish_post_phase() -> None:
            nonlocal control_id
            if control_id is None:
                return
            finish_post_scan(control_id)
            control_id = None
            progress.finish_post_scan()

        try:
            prepared, report = run_maintenance(
                actions=selected_actions,
                ratings=_selection_list(ratings),
                post_start_page=post_start_page,
                post_stop_page=post_stop_page,
                target_per_rating=target_per_rating,
                excluded_post_tags=excluded_post_tags,
                score_range_mode=score_range_mode,
                custom_score_min=custom_score_min,
                custom_score_max=custom_score_max,
                minimum_tag_post_count=minimum_tag_post_count,
                tag_start_page=tag_start_page,
                tag_stop_page=tag_stop_page,
                maximum_tag_pages_per_queue=maximum_tag_pages_per_queue,
                ai_batch_size=ai_batch_size,
                maximum_ai_batches=maximum_ai_batches,
                progress_callback=progress.update_request,
                page_callback=progress.update_page,
                tag_page_callback=progress.update_tag_page,
                stop_requested=(
                    (lambda: post_scan_stop_requested(control_id))
                    if control_id is not None
                    else None
                ),
                post_phase_finished=finish_post_phase,
            )
        finally:
            finish_post_phase()
            progress.finish()
        if not prepared.batch_tokens:
            blocker = ExecutionBlocker(None)
            return io.NodeOutput(
                blocker,
                blocker,
                blocker,
                blocker,
                report,
            )
        return io.NodeOutput(
            prepared.system_prompts,
            prepared.user_prompts,
            prepared.review_system_prompts,
            prepared.batch_tokens,
            report,
        )
