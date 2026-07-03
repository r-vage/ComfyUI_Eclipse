import os
from datetime import datetime
import folder_paths #type: ignore
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY
from ..core.logger import log

_LOG_PREFIX = "FolderPath"

def _format_datetime(datetime_format):
    try:
        return datetime.now().strftime(datetime_format)
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")

class RvFolder_FolderPath(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Folder Path [Eclipse]",
            display_name="Folder Path",
            category=CATEGORY.MAIN.value + CATEGORY.FOLDER.value,
            description="Builds an output folder path from root folder, optional date/time subfolder, and optional batch subfolder. Outputs the full path as a string.",
            inputs=[
                io.String.Input("root_folder", default="images", tooltip="Root folder name under the ComfyUI output directory."),
                io.Boolean.Input("create_date_time_folder", default=True, label_on="yes", label_off="no", socketless=True, tooltip="Append a date/time subfolder to the path."),
                io.String.Input("date_time_format", default="%Y-%m-%d", tooltip="Date/time format string (strftime syntax)."),
                io.Combo.Input("date_time_position", options=["prefix", "postfix"], default="postfix", tooltip="Append date/time before (prefix) or after (postfix) the root folder name."),
                io.Boolean.Input("create_batch_folder", default=False, label_on="yes", label_off="no", socketless=True, tooltip="Append a batch subfolder to the path."),
                io.String.Input("batch_folder_name", default="batch_{}", tooltip="Batch subfolder name. Use {} for batch number substitution."),
                io.Int.Input("batch_number", default=1, min=1, max=0xffffffffffffffff, tooltip="Batch number substituted into batch_folder_name."),
                io.Combo.Input("batch_number_control", options=["fixed", "increment"], default="fixed", tooltip="fixed: keep batch number; increment: auto-increment after each queue."),
            ],
            outputs=[
                io.String.Output("path", tooltip="Full output folder path."),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, root_folder, create_date_time_folder, date_time_format,
                date_time_position, create_batch_folder, batch_folder_name, batch_number,
                batch_number_control):
        tag = f"{_LOG_PREFIX} #{cls.hidden.unique_id}"

        if not isinstance(root_folder, str) or not root_folder.strip():
            root_folder = "images"
        if not isinstance(date_time_format, str) or not date_time_format.strip():
            date_time_format = "%Y-%m-%d"
        if not isinstance(batch_folder_name, str) or not batch_folder_name.strip():
            batch_folder_name = "batch_{}"
        if not isinstance(batch_number, int) or batch_number < 1:
            batch_number = 1
        if batch_number_control not in ("fixed", "increment"):
            batch_number_control = "fixed"

        new_path = root_folder

        if create_date_time_folder:
            dt = _format_datetime(date_time_format)
            if date_time_position == "prefix":
                new_path = os.path.join(dt, root_folder)
            else:
                new_path = os.path.join(root_folder, dt)

        if create_batch_folder:
            try:
                folder_name = batch_folder_name.format(batch_number)
            except Exception:
                folder_name = batch_folder_name
            new_path = os.path.join(new_path, folder_name)

        path_out = os.path.join(folder_paths.get_output_directory(), new_path)
        log.debug(tag, f"path={path_out}")
        return io.NodeOutput(path_out)
