from enum import Enum
from typing import Iterable

__all__ = ["TEXTS", "CATEGORY", "KEYS", "category_display"]


class TEXTS(Enum):
    CUSTOM_NODE_NAME = "Eclipse"
    LOGGER_PREFIX = "Eclipse"
    CONCAT = "concatenated"
    INACTIVE_MSG = "inactive"
    INVALID_METADATA_MSG = "Invalid metadata raw"
    FILE_NOT_FOUND = "File not found!"


class CATEGORY(Enum):
    MAIN = "🌒 Eclipse"
    CONDITIONING = "/ Conditioning"
    CONVERSION = "/ Conversion"
    LOADER = "/ Loader"
    FOLDER = "/ Folder"
    IMAGE = "/ Image"
    IMAGE_LOADERS = "/ Image/Loaders"
    IMAGE_SAVE_PREVIEW = "/ Image/Save & Preview"
    IMAGE_BATCH = "/ Image/Batch Operations"
    IMAGE_TRANSFORMS = "/ Image/Transforms"
    IMAGE_FX = "/ Image/FX & Color"
    MASK = "/ Mask"
    PIPE = "/ Pipe"
    PRIMITIVE = "/ Primitives"
    ROUTER = "/ Router"
    TYPED = "/ Typed"
    SET_GET = "/ Set-Get"
    SETTINGS = "/ Settings"
    TOOLS = "/ Tools"
    TEXT = "/ Text"
    VIDEO = "/ Video"
    AUDIO = "/ Audio"
    SAMPLER = "/ Sampler"
    TESTS = "/ _for Testing"
    DEPRECATED = "/ Legacy"


def category_display(cat: "CATEGORY") -> str:
    # Return a cleaned, human-friendly string for a CATEGORY value.
    #
    # This strips any leading slashes and surrounding whitespace so it can be
    # shown in UIs without duplicated separators.
    # type: ignore[name-defined]
    return cat.value.lstrip("/ ").strip()


# remember, all keys should be in lowercase!
class KEYS(Enum):
    LIST = "list_string"
    PREFIX = "prefix"


# Sanity check: ensure KEYS values are lowercase to match downstream usage.
def _assert_keys_lowercase(items: Iterable[KEYS]) -> None:
    for k in items:
        if k.value != k.value.lower():
            raise AssertionError(f"KEYS value must be lowercase: {k!r}")


_assert_keys_lowercase(KEYS)
