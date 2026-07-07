import os
import shutil
from comfy_api.latest import io  # type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "Workflow Migration Tool"

MAPPINGS = {}


class RvTools_WorkflowMigration(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Workflow Migration Tool [Eclipse]",
            display_name="Workflow Migration Tool",
            category=CATEGORY.MAIN.value + CATEGORY.TOOLS.value,
            description="Scans a single ComfyUI workflow .json file or an entire directory of workflows, "
            "and automatically replaces legacy Eclipse v2/v23 node type IDs with the unified v4.0.0 names.",
            inputs=[
                io.String.Input(
                    "path",
                    default="",
                    tooltip="Absolute path to a single .json workflow file or a folder containing workflows",
                ),
                io.String.Input(
                    "mapping_file",
                    default="tools/migration_eclipse.txt, tools/migration_rvtoolsv1.txt, tools/migration_rvtoolsv2.txt",
                    tooltip="Filename or path (absolute or relative to repo root) to a custom migration mappings text file (comma-separated for multiple files)",
                ),
                io.Boolean.Input(
                    "run_migration",
                    default=False,
                    label_on="write changes",
                    label_off="dry run",
                    socketless=True,
                    tooltip="If disabled, only simulates changes (dry run). If enabled, writes updates and creates backups.",
                ),
                io.Boolean.Input(
                    "create_backup",
                    default=True,
                    label_on="yes",
                    label_off="no",
                    socketless=True,
                    tooltip="Create a <filename>.json.bak backup before modifying any file.",
                ),
            ],
            outputs=[
                io.String.Output("logs"),
            ],
        )

    @classmethod
    def execute(
        cls, path: str, mapping_file: str, run_migration: bool, create_backup: bool
    ):
        path = path.strip()
        if not path:
            return io.NodeOutput(
                "Error: Path is empty. Please enter a valid workflow file or folder path."
            )

        if not os.path.exists(path):
            return io.NodeOutput(f"Error: Path '{path}' does not exist.")

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Support comma-separated list of mapping files
        paths_to_load = [p.strip() for p in mapping_file.split(",") if p.strip()]
        active_mappings = {}
        loaded_sources = []

        for mapping_path in paths_to_load:
            resolved_mapping_path = mapping_path
            if not os.path.isabs(resolved_mapping_path):
                alt_path = os.path.join(repo_root, resolved_mapping_path)
                if os.path.exists(alt_path):
                    resolved_mapping_path = alt_path
                else:
                    cwd_path = os.path.abspath(resolved_mapping_path)
                    if os.path.exists(cwd_path):
                        resolved_mapping_path = cwd_path

            if os.path.exists(resolved_mapping_path) and os.path.isfile(
                resolved_mapping_path
            ):
                try:
                    with open(resolved_mapping_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "|" in line:
                                parts = line.split("|", 1)
                                old_name = parts[0].strip()
                                new_name = parts[1].strip()
                                if old_name and new_name:
                                    active_mappings[old_name] = new_name
                    loaded_sources.append(os.path.basename(resolved_mapping_path))
                except Exception as e:
                    log.error(
                        _LOG_PREFIX,
                        f"Failed to read mapping file '{resolved_mapping_path}': {e}",
                    )

        # Fallback to hardcoded MAPPINGS if file is empty or not found
        using_fallback = False
        if not active_mappings:
            active_mappings = MAPPINGS
            using_fallback = True

        files_to_process = []
        if os.path.isfile(path):
            if path.lower().endswith(".json"):
                files_to_process.append(path)
            else:
                return io.NodeOutput(f"Error: '{path}' is not a .json file.")
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(".json"):
                        files_to_process.append(os.path.join(root, f))

        if not files_to_process:
            return io.NodeOutput(f"No .json files found at '{path}'.")

        mode_str = (
            "WRITE MODE" if run_migration else "DRY-RUN MODE (No changes written)"
        )
        map_source = (
            "None (Using Fallback Mappings)"
            if using_fallback
            else ", ".join(loaded_sources)
        )
        log_lines = [
            "============================================================",
            "          Eclipse Workflow Migration Execution Log          ",
            "============================================================",
            f"Mode:            {mode_str}",
            f"Backup Enabled:  {create_backup}",
            f"Mapping File(s): {map_source}",
            f"Files Found:     {len(files_to_process)} file(s)",
            "------------------------------------------------------------",
            "",
        ]

        total_changed = 0

        import comfy.utils

        pbar = comfy.utils.ProgressBar(len(files_to_process))

        for file_path in files_to_process:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception as e:
                log_lines.append(f"❌ Read Error [{os.path.basename(file_path)}]: {e}")
                pbar.update(1)
                continue

            replacements = {}
            new_content = content

            # Sort active_mappings by key length in descending order to avoid substring replacement issues
            sorted_mappings = sorted(
                active_mappings.items(), key=lambda x: len(x[0]), reverse=True
            )
            for old, new in sorted_mappings:
                count = new_content.count(old)
                if count > 0:
                    new_content = new_content.replace(old, new)
                    replacements[old] = count

            if not replacements:
                log_lines.append(
                    f"• [Skip] {os.path.basename(file_path)} — No legacy Eclipse/RvTools node IDs found."
                )
                pbar.update(1)
                continue

            total_changed += 1
            log_lines.append(f"✓ [Found] {os.path.basename(file_path)}")
            for node, count in replacements.items():
                log_lines.append(f"    - '{node}' -> replaced {count} time(s)")

            if run_migration:
                # Backup
                if create_backup:
                    backup_path = file_path + ".bak"
                    try:
                        shutil.copy2(file_path, backup_path)
                        log_lines.append(
                            f"    - Backup saved: {os.path.basename(backup_path)}"
                        )
                    except Exception as e:
                        log_lines.append(f"    ❌ Backup Failed: {e}")
                        pbar.update(1)
                        continue

                # Write
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    log_lines.append("    - Successfully migrated file.")
                except Exception as e:
                    log_lines.append(f"    ❌ Write Failed: {e}")

            pbar.update(1)
            log_lines.append("")

        log_lines.append("------------------------------------------------------------")
        if run_migration:
            log_lines.append(
                f"Migration finished. Successfully updated {total_changed} file(s)."
            )
        else:
            log_lines.append(
                f"Dry-run finished. Found {total_changed} file(s) that require migration."
            )
        log_lines.append("============================================================")

        full_logs = "\n".join(log_lines)
        log.msg(
            _LOG_PREFIX, f"Execution completed. {total_changed} files updated/found."
        )
        return io.NodeOutput(full_logs)
