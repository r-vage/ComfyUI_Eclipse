import os
import re
import json
import csv
import folder_paths  # type: ignore
import threading
import atexit

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from ..core import CATEGORY
from ..core.logger import log
from ..core.regex_helper import detect_nsfw_level
from comfy_api.latest import io  # type: ignore


_LOG_PREFIX = "Save Prompt"


# Global variables to store values from pipe_opt for placeholder processing
global_values = {
    # Source file placeholders (for batch captioning/tagging workflows)
    'source_filename': '',     # Filename without extension (for %source_filename placeholder)
    'source_folder': '',       # Immediate parent folder name
    'source_base_folder': '',  # Root folder from input list
    # Internal only (not exposed as placeholder)
    '_json_source_filename': '',  # Filename with extension (for JSON key)
}

# Execution counter for %counter placeholder (persists across calls)
_execution_counter = 0


# ===== Batch File Handle Cache =====
# Keeps file handles open for batch append mode with auto-close after timeout

class BatchFileCache:
    # Manages open file handles for batch append mode with auto-close timeout.
    
    def __init__(self, timeout_seconds: float = 10.0):
        self._handles: Dict[str, Any] = {}  # filepath -> file handle
        self._timers: Dict[str, threading.Timer] = {}  # filepath -> close timer
        self._lock = threading.Lock()
        self._timeout = timeout_seconds
        
        # Register cleanup on exit
        atexit.register(self.close_all)
    
    def get_handle(self, filepath: str, mode: str = 'a', encoding: str = 'utf-8'):
        # Get or create a file handle for the given path.
        # Resets the auto-close timer each time.
        with self._lock:
            # Cancel existing timer if any
            if filepath in self._timers:
                self._timers[filepath].cancel()
                del self._timers[filepath]
            
            # Get or create handle
            if filepath not in self._handles:
                log.debug(_LOG_PREFIX, f"[BatchCache] Opening file handle: {filepath}")
                self._handles[filepath] = open(filepath, mode, encoding=encoding)
            
            # Start new close timer
            timer = threading.Timer(self._timeout, self._close_handle, args=[filepath])
            timer.daemon = True
            timer.start()
            self._timers[filepath] = timer
            
            return self._handles[filepath]
    
    def _close_handle(self, filepath: str):
        # Close a specific file handle (called by timer).
        with self._lock:
            if filepath in self._handles:
                try:
                    self._handles[filepath].close()
                    log.msg(_LOG_PREFIX, f"[BatchCache] Auto-closed file handle after timeout: {filepath}")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"[BatchCache] Error closing file: {e}")
                del self._handles[filepath]
            
            if filepath in self._timers:
                del self._timers[filepath]
    
    def flush(self, filepath: str):
        # Flush a specific file handle.
        with self._lock:
            if filepath in self._handles:
                try:
                    self._handles[filepath].flush()
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"[BatchCache] Error flushing file: {e}")
    
    def close_all(self):
        # Close all open file handles (cleanup).
        with self._lock:
            # Cancel all timers
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            
            # Close all handles
            for filepath, handle in list(self._handles.items()):
                try:
                    handle.close()
                    log.debug(_LOG_PREFIX, f"[BatchCache] Closed file handle: {filepath}")
                except Exception as e:
                    log.warning(_LOG_PREFIX, f"[BatchCache] Error closing file {filepath}: {e}")
            self._handles.clear()
    
    def set_timeout(self, timeout_seconds: float):
        # Update the timeout duration.
        self._timeout = timeout_seconds


# Global batch file cache instance (10 second default timeout)
_batch_file_cache = BatchFileCache(timeout_seconds=10.0)


def reset_global_values():
    # Reset global values to defaults at the start of each execution.
    global global_values
    global_values['source_filename'] = ''
    global_values['source_folder'] = ''
    global_values['source_base_folder'] = ''
    global_values['_json_source_filename'] = ''


class FilenameProcessor:
    # Handles filename placeholder processing.
    
    def __init__(self):
        self.placeholders = {
            # Date/time placeholders
            '%today': self._get_date,
            '%date': self._get_date,
            '%time': self._get_time,
            '%Y': lambda: datetime.now().strftime('%Y'),
            '%y': lambda: datetime.now().strftime('%y'),
            '%m': lambda: datetime.now().strftime('%m'),
            '%M': lambda: datetime.now().strftime('%m'),
            '%d': lambda: datetime.now().strftime('%d'),
            '%D': lambda: datetime.now().strftime('%d'),
            '%H': lambda: datetime.now().strftime('%H'),
            '%S': lambda: datetime.now().strftime('%S'),
            # Source file placeholders (for batch captioning/tagging)
            '%source_filename': lambda: global_values.get('source_filename', ''),
            '%source_folder': lambda: global_values.get('source_folder', ''),
            '%source_base_folder': lambda: global_values.get('source_base_folder', ''),
            # Counter placeholder
            '%counter': lambda: str(_execution_counter),
        }

    @staticmethod
    def _get_date() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_time() -> str:
        return datetime.now().strftime("%H%M%S")

    def get_used_placeholders(self, filename: str) -> List[str]:
        if not isinstance(filename, str):
            log.warning(_LOG_PREFIX, f"Invalid filename type: {type(filename)}")
            return []
        return [p for p in self.placeholders.keys() if p in filename]

    def get_placeholder_value(self, placeholder: str) -> str:
        try:
            if placeholder not in self.placeholders:
                log.debug(_LOG_PREFIX, f"Unknown placeholder: {placeholder}; falling back to name without %")
                return placeholder.lstrip('%')

            value = self.placeholders[placeholder]()

            if value in (None, ''):
                log.debug(_LOG_PREFIX, f"Placeholder {placeholder} resolved to empty; falling back to name without %")
                return placeholder.lstrip('%')

            return value
            
        except Exception as e:
            log.error(_LOG_PREFIX, f"Error getting value for {placeholder}: {e}")
            return ''

    def process_string(self, text: str, is_path: bool) -> str:
        try:
            if not text or not isinstance(text, str):
                log.warning(_LOG_PREFIX, "Invalid text for placeholder processing")
                return "default"

            used_placeholders = self.get_used_placeholders(text)
            if not used_placeholders:
                return text

            result = text
            for placeholder in used_placeholders:
                value = self.get_placeholder_value(placeholder)
                result = result.replace(placeholder, value)

            if is_path:
                return self._sanitize_path(result)
            else:
                return self._sanitize_filename(result)

        except Exception as e:
            log.error(_LOG_PREFIX, f"Error processing placeholders: {e}")
            return "error_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        windows_invalid = '<>:"/\\|?*'
        control_chars = ''.join(chr(i) for i in range(32))
        for char in windows_invalid + control_chars:
            filename = filename.replace(char, '_')
        filename = filename.strip(' .')
        if not filename:
            return "untitled"
        return filename

    @staticmethod
    def _sanitize_path(path: str) -> str:
        from pathlib import Path
        parts = Path(path).parts
        sanitized_parts = []
        for i, part in enumerate(parts):
            # Preserve drive letters (e.g., "C:")
            if i == 0 and len(parts) > 1 and part.endswith(':'):
                sanitized_parts.append(part)
            # Preserve relative path markers (. and ..)
            elif part in ('.', '..'):
                sanitized_parts.append(part)
            else:
                invalid_chars = '<>:"|?*'
                control_chars = ''.join(chr(i) for i in range(32))
                for char in invalid_chars + control_chars:
                    part = part.replace(char, '_')
                part = part.strip(' .')
                if not part:
                    part = "unnamed"
                sanitized_parts.append(part)
        return str(Path(*sanitized_parts)) if sanitized_parts else ""


# Singleton instance
filename_processor = FilenameProcessor()


def string_placeholder(text: str, is_path: bool) -> str:
    # Public interface for placeholder processing.
    return filename_processor.process_string(text, is_path)


_output_dir = folder_paths.get_output_directory()


def _extract_source_filename(filepath: Optional[str]) -> None:
    # Extract source filename and set global values for placeholders.
    if not filepath:
        global_values['source_filename'] = ''
        global_values['_json_source_filename'] = ''
        return
    
    # Store full filename with extension (internal, for JSON key)
    base = os.path.basename(filepath)
    global_values['_json_source_filename'] = base
    
    # Store name without extension (for %source_filename placeholder)
    # Handle common image extensions
    for ext in ['.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff', '.tif']:
        if base.lower().endswith(ext):
            global_values['source_filename'] = base[:-len(ext)]
            return
    # Fallback to splitext
    global_values['source_filename'] = os.path.splitext(base)[0]


def _extract_pipe_values(pipe_opt) -> None:
    # Extract values from pipe_opt and set global values for placeholders.
    if pipe_opt is None:
        return
    
    # Handle both dict and tuple (from context nodes) pipes
    if isinstance(pipe_opt, tuple) and len(pipe_opt) > 0:
        ctx = pipe_opt[0] if isinstance(pipe_opt[0], dict) else {}
    elif isinstance(pipe_opt, dict):
        ctx = pipe_opt
    else:
        log.debug(_LOG_PREFIX, f"Unknown pipe_opt type: {type(pipe_opt)}")
        return
    
    # Extract values from pipe (from LoadImageFromFolder)
    # filepath = full path to image, filename = basename only, path = base folder
    filepath = ctx.get("filepath") or ctx.get("filename") or ""  # Full path to image (fallback for old pipes)
    base_folder = ctx.get("base_path") or ctx.get("path") or ""  # Base folder from input (fallback for old pipes)
    
    if filepath:
        _extract_source_filename(filepath)
        # Also set JSON key from filename with extension
        global_values['_json_source_filename'] = os.path.basename(filepath)
    
    # Derive folder name fields from filepath and base_folder
    # source_folder: immediate parent folder of the file
    if filepath:
        global_values['source_folder'] = os.path.basename(os.path.dirname(filepath))
    
    # source_base_folder: root folder from input list (stays within specified folders)
    if base_folder:
        global_values['source_base_folder'] = os.path.basename(base_folder)
    elif filepath:
        # Fallback: same as source_folder if base_folder not provided
        global_values['source_base_folder'] = os.path.basename(os.path.dirname(filepath))


def _prepare_text(text: str) -> str:
    # Remove line breaks from text to ensure single-line output.
    # Replace various newline characters with spaces
    text = text.replace('\r\n', ' ')
    text = text.replace('\r', ' ')
    text = text.replace('\n', ' ')
    # Collapse multiple spaces into one
    text = re.sub(r' +', ' ', text)
    return text.strip()


def _get_next_counter(output_path: str, filename_prefix: str, delimiter: str, extension: str) -> int:
    # Find the next available counter value for new files.
    if not os.path.exists(output_path):
        return 1
    
    # Pattern to match existing files: prefix_NNNN.ext
    pattern = f"{re.escape(filename_prefix)}{re.escape(delimiter)}(\\d+)\\.{re.escape(extension)}"
    
    existing_counters = []
    for filename in os.listdir(output_path):
        match = re.match(pattern, filename)
        if match:
            existing_counters.append(int(match.group(1)))
    
    if existing_counters:
        existing_counters.sort(reverse=True)
        return existing_counters[0] + 1
    
    return 1


def _get_append_filepath(output_path: str, filename_prefix: str, extension: str) -> str:
    # Get the filepath for append mode (no counter in filename).
    filename = f"{filename_prefix}.{extension}"
    return os.path.join(output_path, filename)


def _save_txt(filepath: str, text: str, append: bool) -> None:
    # Save text to a .txt file.
    mode = 'a' if append else 'w'
    with open(filepath, mode, encoding='utf-8') as f:
        if append and os.path.getsize(filepath) > 0:
            f.write('\n')
        f.write(text)


def _save_txt_batch(filepath: str, text: str) -> None:
    # Save text to a .txt file using batch mode (keeps handle open).
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    handle = _batch_file_cache.get_handle(filepath, mode='a', encoding='utf-8')
    
    if file_exists:
        handle.write('\n')
    handle.write(text)
    _batch_file_cache.flush(filepath)


def _save_csv(filepath: str, text: str, append: bool,
              positive_name: str = "", negative_prompt: str = "") -> None:
    # Save text to a .csv file in single-line format: name,prompt,negative_prompt.
    # Each entry is one row with all three columns.
    mode = 'a' if append else 'w'
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    
    def escape_csv_field(field):
        # Escape a field for CSV: wrap in quotes if contains comma, quote, or newline.
        if not field:
            return '""'
        if ',' in field or '"' in field or '\n' in field:
            return '"' + field.replace('"', '""') + '"'
        return '"' + field + '"'
    
    # Prepare negative prompt (remove line breaks for single-line CSV)
    clean_negative = ""
    if negative_prompt:
        clean_negative = negative_prompt.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        clean_negative = re.sub(r' +', ' ', clean_negative).strip()
    
    with open(filepath, mode, newline='', encoding='utf-8') as f:
        if not append or not file_exists:
            # Write header for new files
            f.write('name,prompt,negative_prompt\n')
        
        # Write single row with all data: name,prompt,negative_prompt
        row = f"{escape_csv_field(positive_name)},{escape_csv_field(text)},{escape_csv_field(clean_negative)}\n"
        f.write(row)


def _save_csv_batch(filepath: str, text: str,
                    positive_name: str = "", negative_prompt: str = "") -> None:
    # Save text to a .csv file using batch mode (keeps handle open).
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    
    def escape_csv_field(field):
        if not field:
            return '""'
        if ',' in field or '"' in field or '\n' in field:
            return '"' + field.replace('"', '""') + '"'
        return '"' + field + '"'
    
    clean_negative = ""
    if negative_prompt:
        clean_negative = negative_prompt.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')
        clean_negative = re.sub(r' +', ' ', clean_negative).strip()
    
    # For batch mode, we need to handle the header specially
    # Check if this is a new file (handle not yet opened)
    is_new_file = filepath not in _batch_file_cache._handles and not file_exists
    
    handle = _batch_file_cache.get_handle(filepath, mode='a', encoding='utf-8')
    
    if is_new_file:
        handle.write('name,prompt,negative_prompt\n')
    
    row = f"{escape_csv_field(positive_name)},{escape_csv_field(text)},{escape_csv_field(clean_negative)}\n"
    handle.write(row)
    _batch_file_cache.flush(filepath)


def _save_json(filepath: str, text: str, append: bool, source_filename: str = "", nsfw_level: str = "") -> None:
    # Save text to a .json file.
    # Format: {"filename": {"prompt": ..., "nsfwLevel": ...}}
    # nsfwLevel is only included when nsfw_level is provided.
    json_data = {}
    
    # Load existing data if appending
    if append and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
                if not isinstance(json_data, dict):
                    json_data = {}
        except (json.JSONDecodeError, KeyError):
            log.warning(_LOG_PREFIX, f"Could not parse existing JSON file, starting fresh: {filepath}")
            json_data = {}
    
    # Use source filename as key, fallback to generic key if not available
    key = source_filename if source_filename else f"entry_{len(json_data) + 1}"
    
    # Build entry data
    entry = {"prompt": text}
    if nsfw_level:
        entry["nsfwLevel"] = nsfw_level
    
    # Add/update entry
    json_data[key] = entry
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, indent=4, ensure_ascii=False)
    
    if nsfw_level:
        log.msg(_LOG_PREFIX, f"JSON entry: {key} -> nsfwLevel: {nsfw_level}")
    else:
        log.msg(_LOG_PREFIX, f"JSON entry: {key}")


class RvText_SavePrompt(io.ComfyNode):
    # Save text/prompt to a file in txt, csv, or json format.
    # Supports creating new files with auto-numbering or appending to existing files.
    # Supports placeholders like %source_filename, %source_folder, %source_base_folder, %date, etc.
    
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Save Prompt [Eclipse]",
            display_name="Save Prompt",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            is_output_node=True,
            inputs=[
                io.String.Input("text", force_input=True, tooltip="The text/prompt to save to file."),
                io.String.Input("output_path", default="", tooltip="Output folder path. Leave empty and enable use_source_folder to save alongside source images. Supports placeholders: %source_folder, %source_base_folder, %counter, %date, %time."),
                io.Boolean.Input("use_source_folder", default=True, tooltip="When enabled, saves files in the same folder as the source image (from pipe). Ignores output_path."),
                io.String.Input("filename_prefix", default="%source_filename", tooltip="Prefix for the filename. Supports placeholders: %source_filename (recommended for batch captioning), %source_folder, %source_base_folder, %counter, %date, %time, etc."),
                io.String.Input("filename_delimiter", default="_", tooltip="Delimiter between filename parts."),
                io.Int.Input("filename_number_padding", default=4, min=1, max=9, step=1, tooltip="Number of digits for the counter (e.g., 4 = 0001). Only used in 'new' mode."),
                io.Combo.Input("extension", options=["txt", "csv", "json"], default="txt", tooltip="File format: txt (plain text), csv (name,prompt,negative_prompt), json."),
                io.Combo.Input("write_mode", options=["new", "overwrite", "append", "append_batch", "keep"], default="new", tooltip="new: numbered files (prefix_0001.txt), overwrite: overwrites existing file, append: adds text to file (opens/closes each time), append_batch: keeps file open for fast batch processing (auto-closes after 10s idle), keep: skip if file exists."),
                io.String.Input("csv_positive_name", default="\u2705Style", tooltip="[CSV] Name/label for the style entry (e.g., '\u2705Line Art / Manga')."),
                io.String.Input("csv_negative_prompt", default="ugly, deformed, noisy, low poly, blurry, painting", multiline=True, tooltip="[CSV] Negative prompt text for the style."),
                io.Combo.Input("nsfw_level", options=["disabled", "auto", "None", "Mature", "X"], default="disabled", tooltip="[JSON only] NSFW level tagging. 'auto' detects from text keywords."),
                io.Boolean.Input("log_prompt", default=False, label_on="yes", label_off="no", tooltip="Log the saved prompt to console."),
                # Optional inputs
                io.String.Input("filename_opt", default=None, force_input=True, optional=True, tooltip="Optional: Full filepath to source file (e.g., 'D:/images/cat.png'). Enables %source_filename and %source_folder placeholders without needing a pipe."),
                io.Custom("PIPE").Input("pipe_opt", optional=True, tooltip="Optional pipe from LoadImageFromFolder. Enables placeholders like %source_filename, %source_folder, %source_base_folder, etc. Overrides filename_opt if both connected."),
            ],
            outputs=[
                io.String.Output("text"),
            ],
        )

    @classmethod
    def execute(cls, text, output_path, use_source_folder, filename_prefix,
                filename_delimiter, filename_number_padding, extension, write_mode,
                csv_positive_name="\u2705Style", csv_negative_prompt="",
                nsfw_level="disabled", log_prompt=False,
                filename_opt=None, pipe_opt=None):
        # Execute the save prompt node.
        
        # Reset global values to prevent stale data from previous executions
        reset_global_values()
        
        # Increment execution counter for %counter placeholder
        global _execution_counter
        _execution_counter += 1
        
        # Extract values from pipe_opt for placeholder processing (overrides filename_opt)
        _extract_pipe_values(pipe_opt)
        
        # Fallback: use filename_opt if pipe didn't provide source info
        if filename_opt and not global_values.get('source_filename'):
            _extract_source_filename(filename_opt)
            # Also derive source_folder from the filepath
            global_values['source_folder'] = os.path.basename(os.path.dirname(filename_opt))
        
        # Determine actual nsfw_level for JSON output (only applies to JSON extension)
        actual_nsfw_level = ""
        if extension == "json" and nsfw_level != "disabled":
            if nsfw_level == "auto":
                actual_nsfw_level = detect_nsfw_level(text)
            else:
                actual_nsfw_level = nsfw_level
        
        # Get source folder and base folder path from pipe or filename_opt if use_source_folder is enabled
        source_folder = None
        base_folder_path = None  # Full path to the root folder from LoadImageFromFolder
        if use_source_folder:
            # Try to get filepath (full path) and path (base folder) from pipe_opt first
            if pipe_opt is not None:
                if isinstance(pipe_opt, dict):
                    source_folder = os.path.dirname(pipe_opt.get("filepath") or pipe_opt.get("filename") or "")
                    base_folder_path = pipe_opt.get("base_path") or pipe_opt.get("path") or ""
                elif isinstance(pipe_opt, tuple) and len(pipe_opt) > 0 and isinstance(pipe_opt[0], dict):
                    ctx = pipe_opt[0]
                    source_folder = os.path.dirname(ctx.get("filepath") or ctx.get("filename") or "")
                    base_folder_path = ctx.get("base_path") or ctx.get("path") or ""
            # Fallback to filename_opt if pipe didn't provide source_folder
            if not source_folder and filename_opt:
                source_folder = os.path.dirname(filename_opt)
        
        # Prepare text (remove line breaks)
        clean_text = _prepare_text(text)
        
        # Skip saving if text is empty (no error, just return)
        if not clean_text or clean_text.strip() == '':
            log.debug(_LOG_PREFIX, "Skipping save - input text is empty")
            return io.NodeOutput(text)
        
        # Process placeholders in output_path and filename_prefix
        output_path = string_placeholder(output_path, True) if output_path else output_path
        filename_prefix = string_placeholder(filename_prefix, False)
        
        # Sanitize filename prefix after placeholder processing
        filename_prefix = FilenameProcessor._sanitize_filename(filename_prefix)
        
        # Fallback to source_filename if prefix is empty or "untitled"
        if filename_prefix in ['', 'untitled'] and global_values.get('source_filename'):
            filename_prefix = global_values['source_filename']
        
        # Setup output path
        use_source = False
        if use_source_folder and source_folder:
            if output_path in [None, '', 'none', '.', './', '.\\']:
                # Use the source image's folder directly (allows saving outside ComfyUI)
                output_path = os.path.abspath(source_folder)
            else:
                # Auto-correct single dot prefix to double dot (go up one level)
                # User typing ".\captions" likely means "outside this folder" not "inside"
                if output_path.startswith('.\\') and not output_path.startswith('..\\'):
                    output_path = '..' + output_path[1:]  # .\captions -> ..\captions
                elif output_path.startswith('./') and not output_path.startswith('../'):
                    output_path = '..' + output_path[1:]  # ./captions -> ../captions
                
                # Prevent duplication when %source_folder or %source_base_folder was used
                # e.g., if source_folder = "D:\path\face" and output_path = "face\captions"
                # we'd get "D:\path\face\face\captions" without this fix
                source_folder_name = os.path.basename(source_folder)
                source_base_folder_name = global_values.get('source_base_folder', '')
                
                # Normalize separators for comparison
                output_path_normalized = output_path.replace('\\', '/')
                
                # Check if output_path starts with source_base_folder (from %source_base_folder placeholder)
                # In this case, we should use base_folder_path as the root, not source_folder
                use_base_folder_as_root = False
                if source_base_folder_name and output_path_normalized.startswith(source_base_folder_name + '/'):
                    # Strip the base folder name and use base_folder_path as root
                    output_path = output_path[len(source_base_folder_name) + 1:]
                    use_base_folder_as_root = True
                    log.debug(_LOG_PREFIX, f"Using base folder as root, stripped: {source_base_folder_name}")
                elif source_base_folder_name and output_path_normalized == source_base_folder_name:
                    output_path = ""
                    use_base_folder_as_root = True
                    log.debug(_LOG_PREFIX, f"Using base folder directly: {source_base_folder_name}")
                
                # Strip leading source_folder name if present (handles %source_folder duplication)
                output_path_normalized = output_path.replace('\\', '/')
                if output_path_normalized.startswith(source_folder_name + '/'):
                    output_path = output_path[len(source_folder_name) + 1:]
                    log.debug(_LOG_PREFIX, f"Stripped duplicate source_folder from output_path: {source_folder_name}")
                elif output_path_normalized == source_folder_name:
                    output_path = ""  # Just use source folder directly
                
                # Determine the base path for joining
                if use_base_folder_as_root and base_folder_path:
                    join_base = base_folder_path
                else:
                    join_base = source_folder
                
                # Use output_path as relative to the determined base
                # Supports: "captions" -> base/captions
                #           "../captions" -> parent_of_base/captions
                if output_path:
                    output_path = os.path.abspath(os.path.join(join_base, output_path))
                else:
                    output_path = os.path.abspath(join_base)
            use_source = True
        elif output_path in [None, '', 'none', '.', './', '.\\']:
            output_path = _output_dir
        else:
            # Handle absolute paths when use_source_folder is True but no source folder available
            # OR when use_source_folder is False
            is_absolute = os.path.isabs(output_path)
            
            # If user provided an absolute path, use it directly (allows saving outside ComfyUI)
            if is_absolute:
                output_path = os.path.abspath(output_path)
                use_source = True  # Skip ComfyUI output folder restrictions
            else:
                # Only sanitize relative paths to avoid corrupting absolute paths
                output_path = FilenameProcessor._sanitize_path(output_path)
        
        # Only apply ComfyUI output folder restrictions for relative paths when not using source folder
        if not use_source:
            # Always resolve to absolute path inside ComfyUI output folder
            comfy_output_dir = os.path.abspath(_output_dir)
            if not os.path.isabs(output_path):
                output_path = os.path.normpath(output_path)
                if output_path.startswith('.' + os.sep):
                    output_path = output_path[2:]
                output_path = os.path.join(comfy_output_dir, output_path)
            output_path = os.path.abspath(output_path)
            
            # Force output_path to be inside comfy_output_dir
            if not output_path.startswith(comfy_output_dir):
                rel_path = os.path.relpath(output_path, start=os.path.splitdrive(output_path)[0] or '/')
                output_path = os.path.join(comfy_output_dir, rel_path)
                output_path = os.path.abspath(output_path)
        
        # Create output directory if it doesn't exist
        if not os.path.exists(output_path):
            log.warning(_LOG_PREFIX, f'The path `{output_path}` doesn\'t exist! Creating directory.')
            os.makedirs(output_path, exist_ok=True)
        
        # Determine file path based on write mode
        use_batch = False  # Default, only append_batch uses batch mode
        
        if write_mode == "new":
            # First check if base file exists (without counter)
            base_filepath = _get_append_filepath(output_path, filename_prefix, extension)
            if not os.path.exists(base_filepath):
                # No existing file - create without counter
                filepath = base_filepath
            else:
                # Base file exists - find next available counter
                counter = _get_next_counter(output_path, filename_prefix, filename_delimiter, extension)
                filename = f"{filename_prefix}{filename_delimiter}{counter:0{filename_number_padding}}.{extension}"
                filepath = os.path.join(output_path, filename)
            append = False
        elif write_mode == "overwrite":
            # Single file, overwrite each time (no counter, no append)
            filepath = _get_append_filepath(output_path, filename_prefix, extension)
            append = False
        elif write_mode == "keep":
            # Skip if file already exists (useful for re-running batch without overwriting edits)
            filepath = _get_append_filepath(output_path, filename_prefix, extension)
            if os.path.exists(filepath):
                log.msg(_LOG_PREFIX, f"File already exists, skipping (keep mode): {filepath}")
                if log_prompt:
                    log.msg(_LOG_PREFIX, f"Filepath: {filepath}")
                    log.msg(_LOG_PREFIX, f"Prompt: {clean_text}")
                    if csv_negative_prompt:
                        log.msg(_LOG_PREFIX, f"Negative prompt: {csv_negative_prompt}")
                return io.NodeOutput(text)
            append = False
        elif write_mode == "append_batch":
            # Batch append mode - keeps file handle open for fast sequential writes
            filepath = _get_append_filepath(output_path, filename_prefix, extension)
            append = True  # Always append in batch mode
            use_batch = True
        else:  # append mode
            filepath = _get_append_filepath(output_path, filename_prefix, extension)
            append = os.path.exists(filepath)
        
        # Save based on extension
        try:
            if use_batch:
                # Use batch save methods (keeps file handle open)
                if extension == "txt":
                    _save_txt_batch(filepath, clean_text)
                elif extension == "csv":
                    _save_csv_batch(filepath, clean_text,
                                    csv_positive_name, csv_negative_prompt)
                elif extension == "json":
                    # JSON batch not supported - use regular append
                    json_key_filename = global_values.get('_json_source_filename', '')
                    _save_json(filepath, clean_text, append, json_key_filename, actual_nsfw_level)
            else:
                # Regular save methods
                if extension == "txt":
                    _save_txt(filepath, clean_text, append)
                elif extension == "csv":
                    _save_csv(filepath, clean_text, append,
                              csv_positive_name, csv_negative_prompt)
                elif extension == "json":
                    # Get source filename with extension for JSON key (internal variable)
                    json_key_filename = global_values.get('_json_source_filename', '')
                    _save_json(filepath, clean_text, append, json_key_filename, actual_nsfw_level)
            
            # Only log save confirmation for non-batch saves (batch mode is silent for performance)
            if not use_batch:
                log.msg(_LOG_PREFIX, f"Prompt saved to: {filepath}")
            if log_prompt:
                log.msg(_LOG_PREFIX, f"Filepath: {filepath}")
                log.msg(_LOG_PREFIX, f"Prompt: {clean_text}")
                if csv_negative_prompt:
                    log.msg(_LOG_PREFIX, f"Negative prompt: {csv_negative_prompt}")
            
        except OSError as e:
            log.error(_LOG_PREFIX, f'Unable to save file to: {filepath}')
            log.error(_LOG_PREFIX, str(e))
        except Exception as e:
            log.error(_LOG_PREFIX, f'Unable to save file due to error: {e}')
        
        # Return the original input text as-is
        return io.NodeOutput(text)
