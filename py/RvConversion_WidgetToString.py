import re
from ..core import CATEGORY
from comfy_api.latest import io #type: ignore

# Inline pattern to avoid regex_patterns dependency
RE_NEWLINES = re.compile(r'[\r\n]+', re.IGNORECASE)

class RvConversion_WidgetToString(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Widget to String [Eclipse]",
            display_name="Widget to String",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.Int.Input("id", default=0, min=0, max=100000, step=1, tooltip="Node ID to extract widget value from."),
                io.String.Input("widget_name", multiline=False, tooltip="Name of the widget to extract."),
                io.Boolean.Input("return_all", default=False, tooltip="Return all widget values as a formatted string."),
                io.AnyType.Input("any_input", optional=True),
                io.String.Input("node_title", multiline=False, optional=True, tooltip="Node title to match instead of ID."),
                io.Int.Input("allowed_float_decimals", default=2, min=0, max=10, optional=True, tooltip="Number of decimal places to display for float values."),
            ],
            outputs=[
                io.String.Output("string"),
            ],
            hidden=[io.Hidden.extra_pnginfo, io.Hidden.prompt, io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, id: int, widget_name: str, return_all: bool = False,
                any_input=None, node_title: str = "", allowed_float_decimals: int = 2) -> io.NodeOutput:
        extra_pnginfo = cls.hidden.extra_pnginfo
        prompt = cls.hidden.prompt
        unique_id = cls.hidden.unique_id

        workflow = extra_pnginfo["workflow"]
        results = []
        node_id = None
        link_id = None
        link_to_node_map = {}

        for node in workflow["nodes"]:
            if node_title:
                if "title" in node and node["title"] == node_title:
                    node_id = node["id"]
                    break
            elif id != 0:
                if node["id"] == id:
                    node_id = id
                    break
            elif any_input is not None:
                if node["type"] == "Widget to String [Eclipse]" and node["id"] == int(unique_id) and not link_id:
                    for node_input in node["inputs"]:
                        if node_input["name"] == "any_input":
                            link_id = node_input["link"]
                node_outputs = node.get("outputs", None)
                if not node_outputs:
                    continue
                for output in node_outputs:
                    node_links = output.get("links", None)
                    if not node_links:
                        continue
                    for link in node_links:
                        link_to_node_map[link] = node["id"]
                        if link_id and link == link_id:
                            break

        if link_id:
            node_id = link_to_node_map.get(link_id, None)

        if node_id is None:
            raise ValueError("No matching node found for the given title or id")

        values = prompt[str(node_id)]
        if "inputs" in values:
            if return_all:
                formatted_items = []
                for k, v in values["inputs"].items():
                    if isinstance(v, float):
                        item = f"{k}: {v:.{allowed_float_decimals}f}"
                    else:
                        item = f"{k}: {str(v)}"
                    formatted_items.append(item)
                result = ', '.join(formatted_items)
                # Replace all line breaks with spaces for prompt output
                result = RE_NEWLINES.sub(" ", result)
                results.append(result)
            elif widget_name in values["inputs"]:
                v = values["inputs"][widget_name]
                if isinstance(v, float):
                    v = f"{v:.{allowed_float_decimals}f}"
                else:
                    v = str(v)
                # Replace all line breaks with spaces for prompt output
                v = RE_NEWLINES.sub(" ", v)
                return io.NodeOutput(v)
            else:
                raise NameError(f"Widget not found: {node_id}.{widget_name}")
        return io.NodeOutput(', '.join(results).strip(', '))
