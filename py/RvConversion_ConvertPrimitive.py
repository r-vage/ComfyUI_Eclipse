from ..core import CATEGORY
from ..core.logger import log
from typing import Any
from comfy_api.latest import io  # type: ignore

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
        if "<" in result and "object at 0x" in result:
            return f"[Object: {result}]"
        return result
    except Exception:
        return repr(value)


def _convert_to_combo(input_val):
    # Convert input to COMBO format: (selected_value, [options_list])
    from collections.abc import Iterable

    if isinstance(input_val, Iterable) and not isinstance(
        input_val, (str, bytes, bytearray)
    ):
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


def _evaluate_bool(val: Any) -> bool:
    # Safely evaluate the truthiness of any value
    if isinstance(val, bool):
        return val
    elif isinstance(val, (int, float)):
        return bool(val)
    elif isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in ("true", "yes", "on", "1"):
            return True
        elif cleaned in ("false", "no", "off", "0", ""):
            return False
        else:
            return bool(cleaned)
    elif hasattr(val, "any") and callable(val.any):
        try:
            return bool(val.any())
        except Exception:
            return True
    else:
        try:
            return bool(val)
        except Exception:
            return val is not None


def _convert_value(val: Any, convert_to: str) -> Any:
    # Convert a single value to the target type.
    if convert_to == "STRING":
        if isinstance(val, dict):
            result = str(val)
        elif isinstance(val, bool):
            result = "true" if val else "false"
        else:
            result = str(val)
        result = result.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        return " ".join(result.split())

    elif convert_to == "INT":
        if isinstance(val, bool):
            return 1 if val else 0
        elif isinstance(val, (int, float)):
            return int(val)
        elif isinstance(val, str):
            cleaned = val.strip().lower()
            if cleaned in ("true", "yes", "on", "1"):
                return 1
            elif cleaned in ("false", "no", "off", "0", ""):
                return 0
            else:
                try:
                    return int(float(cleaned))
                except Exception:
                    return 1 if _evaluate_bool(val) else 0
        else:
            try:
                return int(float(val))
            except Exception:
                return 1 if _evaluate_bool(val) else 0

    elif convert_to == "FLOAT":
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        elif isinstance(val, (int, float)):
            return float(val)
        elif isinstance(val, str):
            cleaned = val.strip().lower()
            if cleaned in ("true", "yes", "on", "1"):
                return 1.0
            elif cleaned in ("false", "no", "off", "0", ""):
                return 0.0
            else:
                try:
                    return float(cleaned)
                except Exception:
                    return 1.0 if _evaluate_bool(val) else 0.0
        else:
            try:
                return float(val)
            except Exception:
                return 1.0 if _evaluate_bool(val) else 0.0

    elif convert_to == "BOOLEAN":
        return _evaluate_bool(val)

    return val


class RvConversion_ConvertPrimitive(io.ComfyNode):
    # Convert any input to primitive types: STRING, INT, FLOAT, BOOLEAN, or COMBO.
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Convert Primitive [Eclipse]",
            display_name="Convert Primitive",
            category=CATEGORY.MAIN.value + CATEGORY.CONVERSION.value,
            inputs=[
                io.AnyType.Input(
                    "input",
                    tooltip="Any value to convert",
                ),
                io.Combo.Input(
                    "convert_to",
                    options=["STRING", "INT", "FLOAT", "BOOLEAN", "COMBO"],
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
        # Helper to flatten nested lists/tuples
        def flatten_list(lst):
            flat = []
            def _process(item):
                if isinstance(item, (list, tuple)):
                    for sub in item:
                        _process(sub)
                elif item is not None:
                    flat.append(item)
            _process(lst)
            return flat

        try:
            # Check if the input is a list/tuple
            is_seq = isinstance(input, (list, tuple))
            if is_seq:
                flat_input = flatten_list(input)
            else:
                flat_input = input

            # Handle COMBO type separately
            if convert_to == "COMBO":
                return io.NodeOutput(*_convert_to_combo(flat_input))

            # For other target types:
            if is_seq:
                # Convert each element of the flattened list
                result = [_convert_value(item, convert_to) for item in flat_input]
            else:
                result = _convert_value(flat_input, convert_to)

            return io.NodeOutput(result)

        except Exception as e:
            log.error(_LOG_PREFIX, f"Conversion error: {e}")
            if convert_to == "STRING":
                return io.NodeOutput("")
            elif convert_to == "INT":
                return io.NodeOutput(0)
            elif convert_to == "FLOAT":
                return io.NodeOutput(0.0)
            elif convert_to == "BOOLEAN":
                return io.NodeOutput(False)

        return io.NodeOutput("")
