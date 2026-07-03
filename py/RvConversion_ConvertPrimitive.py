from ..core import CATEGORY
from ..core.logger import log
from typing import Any
from comfy_api.latest import io #type: ignore

_LOG_PREFIX = "Convert"


def _scalar_to_str(value):
    # Convert a scalar value to a safe string for combo use.
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    
    # Convert to string and check if it's an unhelpful object representation
    try:
        result = f"{value}"
        if '<' in result and 'object at 0x' in result:
            return f"[Object: {result}]"
        return result
    except Exception:
        return repr(value)


def _convert_to_combo(input_val):
    # Convert input to COMBO format: (selected_value, [options_list])
    from collections.abc import Iterable
    
    if isinstance(input_val, Iterable) and not isinstance(input_val, (str, bytes, bytearray)):
        try:
            options = [_scalar_to_str(item) for item in input_val]
            if len(options) == 0:
                return (("", [""]),)
            return ((options[0], options),)
        except Exception as e:
            log.error(_LOG_PREFIX, f"COMBO conversion from iterable failed: {e}")
            return (("", [""]),)
    else:
        str_val = _scalar_to_str(input_val)
        return ((str_val, [str_val]),)


class RvConversion_ConvertPrimitive(io.ComfyNode):
    # Convert any input to primitive types: STRING, INT, FLOAT, or COMBO.
    # Handles single values only - does not accept list inputs.
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Convert Primitive [Eclipse]",
            display_name="Convert Primitive",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.AnyType.Input("input", tooltip="Any value to convert (single values only)"),
                io.Combo.Input(
                    "convert_to",
                    options=["STRING", "INT", "FLOAT", "COMBO"],
                    default="STRING",
                    tooltip="Target primitive type",
                ),
            ],
            outputs=[
                io.AnyType.Output("output"),
            ],
        )

    @classmethod
    def execute(cls, input: Any, convert_to: str) -> io.NodeOutput:
        # Handle COMBO type separately
        if convert_to == "COMBO":
            return io.NodeOutput(*_convert_to_combo(input))
        
        # Check for list/tuple input and reject it
        if isinstance(input, (list, tuple)):
            log.warning(_LOG_PREFIX, "List/tuple input detected. Use ConvertToList node first to extract values.")
            if len(input) > 0:
                input = input[0]
            else:
                input = ""
        
        try:
            result: Any
            if convert_to == "STRING":
                if isinstance(input, dict):
                    result = str(input)
                elif isinstance(input, bool):
                    result = "true" if input else "false"
                else:
                    result = str(input)
                result = result.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                result = ' '.join(result.split())
                return io.NodeOutput(result)
            
            elif convert_to == "INT":
                if isinstance(input, bool):
                    result = 1 if input else 0
                elif isinstance(input, (int, float)):
                    result = int(input)
                elif isinstance(input, str):
                    cleaned = input.strip().lower()
                    if cleaned in ("true", "yes", "on", "1"):
                        result = 1
                    elif cleaned in ("false", "no", "off", "0"):
                        result = 0
                    else:
                        result = int(float(cleaned))
                else:
                    result = 0
                return io.NodeOutput(result)
            
            elif convert_to == "FLOAT":
                if isinstance(input, bool):
                    result = 1.0 if input else 0.0
                elif isinstance(input, (int, float)):
                    result = float(input)
                elif isinstance(input, str):
                    cleaned = input.strip().lower()
                    if cleaned in ("true", "yes", "on"):
                        result = 1.0
                    elif cleaned in ("false", "no", "off"):
                        result = 0.0
                    else:
                        result = float(cleaned)
                else:
                    result = 0.0
                return io.NodeOutput(result)
            
        except (ValueError, TypeError) as e:
            log.error(_LOG_PREFIX, f"Conversion error: {e}")
            if convert_to == "STRING":
                return io.NodeOutput("")
            elif convert_to == "INT":
                return io.NodeOutput(0)
            elif convert_to == "FLOAT":
                return io.NodeOutput(0.0)
        
        return io.NodeOutput("")
