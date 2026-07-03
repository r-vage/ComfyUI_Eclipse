import os
from comfy_api.latest import io #type: ignore
from ..core import CATEGORY

class RvFolder_AddFolder(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Add Folder [Eclipse]",
            display_name="Add Folder",
            category=CATEGORY.MAIN.value + CATEGORY.FOLDER.value,
            inputs=[
                io.String.Input("path", force_input=True, tooltip="Base path to which the folder will be added."),
                io.String.Input("folder_name", multiline=False, default="SubFolder", tooltip="Folder name to join to the base path."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
        )

    @classmethod
    def execute(cls, path, folder_name):
        # Joins a folder name to a base path, returning the new path as a string.
        if not isinstance(path, str) or not path:
            return io.NodeOutput("")
        if not isinstance(folder_name, str) or not folder_name:
            return io.NodeOutput(path)
        new_path = os.path.join(path, folder_name)
        return io.NodeOutput(new_path)