from .keys import *
from .logger import cstr  # cstr is now in logger.py
from .common import *

__all__ = ["keys", "common", "logger", "__version__", "version"]


# Read version from pyproject.toml
def _read_pyproject_version() -> str:
    try:
        from pathlib import Path
        import re

        for parent in Path(__file__).resolve().parents:
            toml_file = parent / "pyproject.toml"
            if not toml_file.exists():
                continue

            content = toml_file.read_text(encoding="utf-8")
            match = re.search(r"\bversion\s*=\s*['\"]([^'\"]+)['\"]", content)
            if match:
                return match.group(1)
    except Exception:
        pass
    return "4.0.10"


try:
    __version__ = _read_pyproject_version()
    version = __version__
except Exception:
    __version__ = "4.0.10"
    version = "4.0.10"
