import json
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, Tuple, List, cast

from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Smart Prompt"
_IGNORED_PROMPT_FOLDERS = frozenset({"tag_lists"})
# Some extension must be setting a seed as server-generated seeds were not random. We'll set a new
# seed and use that state going forward.
initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
eclipse_seed_random_state = random.getstate()
random.setstate(initial_random_state)


def new_random_seed():
    # Gets a new random seed from the eclipse_seed_random_state and resetting the previous state.
    global eclipse_seed_random_state
    prev_random_state = random.getstate()
    random.setstate(eclipse_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    eclipse_seed_random_state = random.getstate()
    random.setstate(prev_random_state)
    return seed


def get_prompt_folders():
    # Get all folders in the repo's prompts/ directory.
    # Deduplicates: if both numbered (e.g. 01_subjects/) and unnumbered (subjects/)
    # folders exist with the same clean name, the numbered one wins.
    # Returns folders sorted by numeric prefix (numbered first, then alphabetical).
    prompt_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "prompts"
    )

    if not os.path.isdir(prompt_dir):
        return []

    # Collect all folders, group by clean name, prefer numbered
    clean_name_map: Dict[str, Tuple[str, float]] = {}  # clean_name → (path, sort_key)
    for item in os.listdir(prompt_dir):
        item_path = os.path.join(prompt_dir, item)
        if not os.path.isdir(item_path):
            continue
        clean_name = re.sub(r"^[0-9_]+", "", item)
        if clean_name in _IGNORED_PROMPT_FOLDERS:
            continue
        num_match = re.match(r"^(\d+)", item)
        has_number = num_match is not None
        sort_key = int(num_match.group(1)) if has_number else float("inf")
        # Prefer numbered folder over unnumbered when both exist
        if clean_name not in clean_name_map or has_number:
            clean_name_map[clean_name] = (item_path, sort_key)

    # Sort by numeric prefix, then alphabetically by clean name
    sorted_items = sorted(clean_name_map.items(), key=lambda x: (x[1][1], x[0]))
    return [item[1][0] for item in sorted_items]


# Module-level state for caching (moved from instance vars for V3 classmethod pattern)
_last_seed = None
_last_output = None
_last_folder = None
_last_widget_values = None
_file_options = None


class RvText_SmartPrompt_All(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        inputs = []

        # Get available folders for the combo box (clean names without numbers)
        prompt_folders = get_prompt_folders()
        folder_names = []
        for folder in prompt_folders:
            folder_name = os.path.basename(folder)
            clean_folder_name = re.sub(r"^[0-9_]+", "", folder_name)
            if clean_folder_name not in folder_names:
                folder_names.append(clean_folder_name)

        folder_options = ["All"] + folder_names
        inputs.append(
            io.Combo.Input(
                "folder",
                options=folder_options,
                default="subjects",
                tooltip="Select folder to load prompt options from, or All to show all folders",
            )
        )

        # Scan all folders and collect widget info
        for folder in prompt_folders:
            if not os.path.isdir(folder):
                continue
            folder_name = os.path.basename(folder)
            clean_folder_name = re.sub(r"^[0-9_]+", "", folder_name)

            # Collect files for this folder
            folder_files = []
            for fname in os.listdir(folder):
                if fname.lower().endswith(".txt") and fname.startswith(
                    ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
                ):
                    try:
                        number = int(fname.split("_")[0])
                        folder_files.append((number, fname))
                    except ValueError:
                        continue

            # Sort files by number
            folder_files.sort(key=lambda x: x[0])

            for number, fname in folder_files:
                base = os.path.splitext(fname)[0]
                clean_base = re.sub(r"^[0-9_]+", "", base).replace("_", " ")
                display = f"{clean_folder_name} {clean_base}"
                fpath = os.path.join(folder, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f if line.strip()]
                        combo_options = ["None", "Random"] + lines
                except Exception:
                    combo_options = ["None"]
                inputs.append(
                    io.Combo.Input(display, options=combo_options, default="None")
                )

        # Add seed as the last parameter
        inputs.append(
            io.Int.Input(
                "seed",
                default=0,
                min=-3,
                max=2**64 - 1,
                tooltip="Random seed for prompt selection.",
            )
        )

        return io.Schema(
            node_id="Smart Prompt [Eclipse]",
            display_name="Smart Prompt",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            inputs=inputs
            + [
                io.Int.Input(
                    "seed_input",
                    default=None,
                    force_input=True,
                    optional=True,
                    tooltip="Optional seed input that overrides the widget seed if connected",
                ),
            ],
            outputs=[
                io.String.Output("prompt"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
        )

    @classmethod
    def validate_inputs(cls, **kwargs):
        # Accept **kwargs so ComfyUI skips built-in combo validation.
        # This prevents "Value not in list" errors for stale filenames
        # in saved workflows (e.g. LoRA files that were moved/deleted).
        # Actual file existence is validated at execution time.
        return True

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Forces a changed state if we happen to get a special seed, as if from the API directly.
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return new_random_seed()
        folder = kwargs.get("folder", "All")
        widget_values = tuple(sorted(kwargs.items()))
        return (seed, folder, widget_values)

    @classmethod
    def execute(cls, seed=0, seed_input=None, **kwargs):
        global _last_seed, _last_output, _last_folder, _last_widget_values

        prompt_data = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id

        # Use seed_input if provided (which will be the actual executed seed from connected node)
        # Otherwise use the widget seed
        original_seed = seed
        if seed_input is not None:
            # seed_input contains the actual executed seed value from the connected node
            seed = seed_input
            original_seed = seed_input

        # Handle special seeds (-1, -2, -3) only if NOT from seed_input
        # (seed_input will already have resolved seeds from the connected node)
        if seed_input is None and seed in (-1, -2, -3):
            if seed in (-2, -3):
                log.warning(
                    _LOG_PREFIX,
                    f'Cannot {"increment" if seed == -2 else "decrement"} seed from '
                    + "server, but will generate a new random seed.",
                )

            seed = new_random_seed()
            log.msg(
                _LOG_PREFIX,
                f"Server-generated random seed {seed} used for random prompt selection.",
            )

            # Save the resolved seed to workflow
            if unique_id is not None and extra_pnginfo is not None:
                workflow_node = next(
                    (
                        x
                        for x in extra_pnginfo["workflow"]["nodes"]
                        if str(x["id"]) == unique_id
                    ),
                    None,
                )
                if workflow_node is not None and "widgets_values" in workflow_node:
                    for index, widget_value in enumerate(
                        workflow_node["widgets_values"]
                    ):
                        if widget_value == original_seed:
                            workflow_node["widgets_values"][index] = seed
                            break

            if prompt_data is not None:
                prompt_node = prompt_data.get(unique_id)
                if (
                    prompt_node is not None
                    and "inputs" in prompt_node
                    and "seed" in prompt_node["inputs"]
                ):
                    prompt_node["inputs"]["seed"] = seed

        # Get selected folder (clean name)
        selected_folder = kwargs.get("folder", "All")

        # Build prompt from selected or random lines
        # Create a cache key that includes seed, folder, and widget values
        widget_values = tuple(sorted(kwargs.items()))

        if (
            _last_seed == seed
            and _last_output is not None
            and _last_folder == selected_folder
            and _last_widget_values == widget_values
        ):
            return io.NodeOutput(_last_output)

        # Store current values for caching
        _last_widget_values = widget_values
        _last_folder = selected_folder

        # Build file map only for selected folder(s)
        file_map = {}
        prompt_folders = get_prompt_folders()

        folders_to_scan = []
        if selected_folder == "All":
            folders_to_scan = prompt_folders
        else:
            # Find folders that match the clean name
            for folder in prompt_folders:
                folder_name = os.path.basename(folder)
                clean_folder_name = re.sub(r"^[0-9_]+", "", folder_name)
                if clean_folder_name == selected_folder:
                    folders_to_scan.append(folder)

        for folder in folders_to_scan:
            if not os.path.isdir(folder):
                continue
            folder_name = os.path.basename(folder)
            clean_folder_name = re.sub(r"^[0-9_]+", "", folder_name)

            # Collect files for this folder
            folder_files = []
            for fname in os.listdir(folder):
                if fname.lower().endswith(".txt") and fname.startswith(
                    ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
                ):
                    try:
                        number = int(fname.split("_")[0])
                        folder_files.append((number, fname))
                    except ValueError:
                        continue

            # Sort files by number
            folder_files.sort(key=lambda x: x[0])

            for number, fname in folder_files:
                base = os.path.splitext(fname)[0]
                # Use clean widget name (same as in INPUT_TYPES)
                clean_base = re.sub(r"^[0-9_]+", "", base).replace("_", " ")
                display = f"{clean_folder_name} {clean_base}"
                fpath = os.path.join(folder, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        lines = [line.strip() for line in f if line.strip()]
                        file_map[display] = lines
                except Exception:
                    file_map[display] = []

        values = []
        random.seed(seed)
        random_selections = (
            {}
        )  # Track which widgets had "Random" and their selected values

        for display, lines in file_map.items():
            val = kwargs.get(display, "None")
            if val == "Random":
                if lines:
                    selected = random.choice(lines)
                    values.append(selected)
                    random_selections[display] = selected  # Store the resolved value
            elif val not in ("None", "disabled"):
                values.append(val.strip())

        # Save resolved random values to workflow metadata (same pattern as seed resolution)
        if random_selections and unique_id is not None and extra_pnginfo is not None:
            workflow_node = next(
                (
                    x
                    for x in extra_pnginfo["workflow"]["nodes"]
                    if str(x["id"]) == unique_id
                ),
                None,
            )
            if workflow_node is not None and "widgets_values" in workflow_node:
                # Rebuild the widget list in the same order as INPUT_TYPES to find correct indices
                widget_order = ["folder"]  # First widget is always 'folder'

                # Rebuild widgets in the same order as INPUT_TYPES
                prompt_folders = get_prompt_folders()
                for folder in prompt_folders:
                    if not os.path.isdir(folder):
                        continue
                    folder_name = os.path.basename(folder)
                    clean_folder_name = re.sub(r"^[0-9_]+", "", folder_name)

                    folder_files = []
                    for fname in os.listdir(folder):
                        if fname.lower().endswith(".txt") and fname.startswith(
                            ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")
                        ):
                            try:
                                number = int(fname.split("_")[0])
                                folder_files.append((number, fname))
                            except ValueError:
                                continue

                    folder_files.sort(key=lambda x: x[0])

                    for number, fname in folder_files:
                        base = os.path.splitext(fname)[0]
                        clean_base = re.sub(r"^[0-9_]+", "", base).replace("_", " ")
                        display = f"{clean_folder_name} {clean_base}"
                        widget_order.append(display)

                widget_order.append("seed")  # Seed is the last widget

                # Update widgets_values with resolved random selections
                for widget_name, selected_value in random_selections.items():
                    if widget_name in widget_order:
                        index = widget_order.index(widget_name)
                        if index < len(workflow_node["widgets_values"]):
                            workflow_node["widgets_values"][index] = selected_value

        # Also update the prompt inputs for consistency
        if random_selections and prompt_data is not None:
            prompt_node = prompt_data.get(unique_id)
            if prompt_node is not None and "inputs" in prompt_node:
                for widget_name, selected_value in random_selections.items():
                    if widget_name in prompt_node["inputs"]:
                        prompt_node["inputs"][widget_name] = selected_value

        # Clean up values: remove trailing punctuation and extra spaces
        values = [re.sub(r"[.,;:!?]+$", "", val.strip()) for val in values]

        # Join with comma and space
        prompt = ", ".join(values)

        # Clean up the final prompt: multiple spaces to single, remove trailing comma
        prompt = re.sub(r"\s+", " ", prompt).strip()
        prompt = re.sub(r",\s*$", "", prompt)

        _last_seed = seed
        _last_output = prompt
        return io.NodeOutput(prompt)
