# Eclipse Logger - Centralized logging with log level filtering.
#
# Includes:
# - cstr class for colored terminal output
# - EclipseLogger for filtered logging
# - Message templates for Eclipse branding
#
# Usage:
#     from .logger import log, cstr
#     
#     log.debug("MyModule", "Debug message")
#     log.info("MyModule", "Info message")  
#     log.warning("MyModule", "Warning message")
#     log.error("MyModule", "Error message")  # Always shown
#     log.msg("MyModule", "Regular message")  # Always shown
#
# Log levels (configured in config.json):
#     error   - Only errors shown
#     warning - Errors + warnings
#     info    - Errors + warnings + info
#     debug   - Everything

import json
from pathlib import Path


# =============================================================================
# cstr - Colored String Class
# =============================================================================

class cstr(str):
    # String subclass with ANSI color support for terminal output.
    
    class color:
        END = '\x1b[0m'
        BOLD = '\x1b[1m'
        ITALIC = '\x1b[3m'
        UNDERLINE = '\x1b[4m'
        BLINK = '\x1b[5m'
        BLINK2 = '\x1b[6m'
        SELECTED = '\x1b[7m'

        BLACK = '\x1b[30m'
        RED = '\x1b[31m'
        GREEN = '\x1b[32m'
        YELLOW = '\x1b[33m'
        BLUE = '\x1b[34m'
        VIOLET = '\x1b[35m'
        BEIGE = '\x1b[36m'
        WHITE = '\x1b[37m'

        BLACKBG = '\x1b[40m'
        REDBG = '\x1b[41m'
        GREENBG = '\x1b[42m'
        YELLOWBG = '\x1b[43m'
        BLUEBG = '\x1b[44m'
        VIOLETBG = '\x1b[45m'
        BEIGEBG = '\x1b[46m'
        WHITEBG = '\x1b[47m'

        GREY = '\x1b[90m'
        LIGHTRED = '\x1b[91m'
        LIGHTGREEN = '\x1b[92m'
        LIGHTYELLOW = '\x1b[93m'
        LIGHTBLUE = '\x1b[94m'
        LIGHTVIOLET = '\x1b[95m'
        LIGHTBEIGE = '\x1b[96m'
        LIGHTWHITE = '\x1b[97m'

        GREYBG = '\x1b[100m'
        LIGHTREDBG = '\x1b[101m'
        LIGHTGREENBG = '\x1b[102m'
        LIGHTYELLOWBG = '\x1b[103m'
        LIGHTBLUEBG = '\x1b[104m'
        LIGHTVIOLETBG = '\x1b[105m'
        LIGHTBEIGEBG = '\x1b[106m'
        LIGHTWHITEBG = '\x1b[107m'

        @staticmethod
        def add_code(name: str, code: str):
            # Add a custom color code at runtime.
            key = name.upper()
            if not hasattr(cstr.color, key):
                setattr(cstr.color, key, code)
            else:
                raise ValueError(f"'cstr' object already contains a code with the name '{name}'.")

    def __new__(cls, text: str, suffix: str = ""):
        combined = f"{text}: {suffix}" if suffix else text
        return super().__new__(cls, combined)

    def __getattr__(self, attr: str):
        # Support attribute-based colorization and class-level access.
        # Handle literal placeholder prefix '_cstr' (exact prefix)
        try:
            if attr.startswith("_cstr"):
                code_name = attr[len("_cstr"):].upper()
                code = getattr(self.color, code_name, None)
                if code is None:
                    raise AttributeError(f"color code '{code_name}' not found")
                modified_text = self.replace(f"__{code_name}__", f"{code}")
                return cstr(modified_text)

            # Direct color attribute (e.g. .RED)
            code = getattr(self.color, attr.upper(), None)
            if code is not None:
                modified_text = f"{code}{self}{self.color.END}"
                return cstr(modified_text)

            # Expose class-level helpers (if any)
            if hasattr(cstr, attr):
                return getattr(cstr, attr)
        except AttributeError:
            pass
        raise AttributeError(f"'cstr' object has no attribute '{attr}'")

    def print(self, **kwargs):
        print(self, **kwargs)


# =============================================================================
# Message Templates - Eclipse Branding
# =============================================================================

# Register Eclipse-branded message templates
cstr.color.add_code("msg", f"{cstr.color.LIGHTGREEN}Eclipse: {cstr.color.END}")
cstr.color.add_code("warning", f"{cstr.color.LIGHTGREEN}Eclipse {cstr.color.LIGHTYELLOW}Warning: {cstr.color.END}")
cstr.color.add_code("debug", f"{cstr.color.LIGHTGREEN}Eclipse {cstr.color.LIGHTBEIGE}Debug: {cstr.color.END}")
cstr.color.add_code("error", f"{cstr.color.RED}Eclipse {cstr.color.END}Error: {cstr.color.END}")


# =============================================================================
# Log Level Configuration
# =============================================================================

# Path to Eclipse node directory
NODE_DIR = Path(__file__).resolve().parent.parent

# Log level hierarchy: error < warning < info < debug
_LOG_LEVELS = {"error": 0, "warning": 1, "info": 2, "debug": 3}


def _get_config_value(key: str, default=None):
    # Get a value from config.json.
    config_path = NODE_DIR / "config.json"
    if not config_path.exists():
        return default
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get(key, default)
    except Exception:
        return default


def get_log_level() -> str:
    # Get current log level from config (error, warning, info, debug).
    val = _get_config_value("log_level", "warning")
    if not isinstance(val, str):
        val = "warning"
    return val.lower()


def _get_log_level_value() -> int:
    # Get numeric log level value for comparison.
    return _LOG_LEVELS.get(get_log_level(), 1)


def is_error_enabled() -> bool:
    # Check if error logging is enabled (always True - errors are always shown).
    return True


def is_warning_enabled() -> bool:
    # Check if warning logging is enabled (warning, info, or debug level).
    return _get_log_level_value() >= _LOG_LEVELS["warning"]


def is_info_enabled() -> bool:
    # Check if info logging is enabled (info or debug level).
    return _get_log_level_value() >= _LOG_LEVELS["info"]


def is_debug_enabled() -> bool:
    # Check if debug logging is enabled via log_level config.
    return _get_log_level_value() >= _LOG_LEVELS["debug"]


class EclipseLogger:
    # Centralized logger with log level filtering.
    
    def _reload_config(self):
        # Force reload of log level from config file (called when config changes).
        pass  # Log level is read dynamically via get_log_level()
    
    def debug(self, prefix: str, message: str):
        # Print debug message only when log_level is 'debug'.
        if is_debug_enabled():
            if prefix.strip():
                cstr(f"[DEBUG {prefix}] {message}").msg.print()
            else:
                cstr(f"[DEBUG] {message}").msg.print()
    
    def info(self, prefix: str, message: str):
        # Print info message only when log_level is 'info' or higher.
        if is_info_enabled():
            if prefix.strip():
                cstr(f"[{prefix}] {message}").msg.print()
            else:
                cstr(message).msg.print()
    
    def warning(self, prefix: str, message: str):
        # Print warning message only when log_level is 'warning' or higher.
        if is_warning_enabled():
            if prefix.strip():
                cstr(f"[WARNING {prefix}] {message}").msg.print()
            else:
                cstr(f"[WARNING] {message}").msg.print()
    
    def error(self, prefix: str, message: str):
        # Print error message (always shown).
        if prefix.strip():
            cstr(f"[ERROR {prefix}] {message}").msg.print()
        else:
            cstr(f"[ERROR] {message}").msg.print()
    
    def msg(self, prefix: str, message: str):
        # Print regular message (always shown, not filtered by log level).
        if prefix.strip():
            cstr(f"[{prefix}] {message}").msg.print()
        else:
            cstr(message).msg.print()


# Singleton instance
log = EclipseLogger()
