import os
from datetime import datetime
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

def format_datetime(datetime_format):
    today = datetime.now()
    try:
        timestamp = today.strftime(datetime_format)
    except Exception:
        timestamp = today.strftime("%Y-%m-%d-%H%M%S")

    return timestamp

def format_date_time(string: str, position: str, datetime_format: str) -> str:
    today = datetime.now()
    if position == "prefix":
        return f"{today.strftime(datetime_format)}_{string}"
    if position == "postfix":
        return f"{string}_{today.strftime(datetime_format)}"
    return string

class RvFolder_FilenamePrefix(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Add Filename Prefix [Eclipse]",
            display_name="Add Filename Prefix",
            category=CATEGORY.MAIN.value + CATEGORY.FOLDER.value,
            inputs=[
                io.String.Input("file_name_prefix", default="image", multiline=False, tooltip="Filename prefix to join to the base path."),
                io.Combo.Input("add_date_time", options=["disable", "prefix", "postfix"], tooltip="Add date/time to the filename prefix."),
                io.String.Input("date_time_format", default="%Y-%m-%d_%H-%M-%S", multiline=False, tooltip="Date/time format for prefix/postfix."),
                io.String.Input("path_opt", optional=True, force_input=True, tooltip="Optional base path to which the filename prefix will be added."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(
        cls,
        file_name_prefix: str,
        add_date_time: str,
        date_time_format: str,
        path_opt: str | None = None,
    ) -> io.NodeOutput:
        # Joins a filename prefix (with optional date/time) to a base path, returning the new path as a string.
        if not isinstance(file_name_prefix, str) or not file_name_prefix:
            file_name_prefix = "image"

        if add_date_time == "disable":
            prefix = file_name_prefix
        else:
            prefix = format_date_time(file_name_prefix, add_date_time, date_time_format)
        if path_opt and isinstance(path_opt, str) and path_opt:
            new_path = os.path.join(path_opt, prefix)
        else:
            new_path = prefix
        return io.NodeOutput(new_path)