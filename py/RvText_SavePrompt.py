import atexit
import csv
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import folder_paths  # type: ignore
from comfy_api.latest import io  # type: ignore

from ..core import CATEGORY
from ..core.json_store import JsonStoreError, update_json_object, write_json_object
from ..core.logger import log
from ..core.regex_helper import detect_nsfw_level

_LOG_PREFIX = "Save Prompt"
_EMPTY_PATHS = {None, "", "none", ".", "./", ".\\"}


# Kept as a compatibility surface for callers of string_placeholder(). Node execution
# uses isolated per-item contexts so concurrent executions cannot exchange filenames.
global_values = {
    "source_filename": "",
    "source_folder": "",
    "source_base_folder": "",
    "_json_source_filename": "",
}

_execution_counter = 0
_execution_counter_lock = threading.Lock()


def _next_execution_counter() -> int:
    global _execution_counter

    with _execution_counter_lock:
        _execution_counter += 1
        return _execution_counter


class BatchFileCache:
    # Serializes complete append operations and closes idle handles safely.

    def __init__(self, timeout_seconds: float = 10.0):
        self._handles: dict[str, TextIO] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.Lock()
        self._timeout = timeout_seconds
        atexit.register(self.close_all)

    def _discard_handle_locked(self, filepath: str) -> None:
        handle = self._handles.pop(filepath, None)
        if handle is not None:
            try:
                handle.close()
            except OSError as error:
                log.warning(
                    _LOG_PREFIX,
                    f"[BatchCache] Error closing file {filepath}: {error}",
                )

    def _schedule_close_locked(self, filepath: str) -> None:
        old_timer = self._timers.pop(filepath, None)
        if old_timer is not None:
            old_timer.cancel()

        generation = self._generations.get(filepath, 0) + 1
        self._generations[filepath] = generation
        timer = threading.Timer(
            self._timeout,
            self._close_handle,
            args=(filepath, generation),
        )
        timer.daemon = True
        self._timers[filepath] = timer
        timer.start()

    def write(
        self,
        filepath: str,
        write_operation: Callable[[TextIO, bool], None],
        *,
        newline: str | None = None,
    ) -> None:
        with self._lock:
            handle = self._handles.get(filepath)
            if handle is None or handle.closed:
                log.debug(_LOG_PREFIX, f"[BatchCache] Opening file handle: {filepath}")
                handle = Path(filepath).open(  # noqa: SIM115 - intentionally cached
                    "a",
                    encoding="utf-8",
                    newline=newline,
                )
                self._handles[filepath] = handle

            is_empty = Path(filepath).stat().st_size == 0
            try:
                write_operation(handle, is_empty)
                handle.flush()
            except (OSError, csv.Error):
                self._discard_handle_locked(filepath)
                raise

            self._schedule_close_locked(filepath)

    def _close_handle(self, filepath: str, generation: int) -> None:
        with self._lock:
            if self._generations.get(filepath) != generation:
                return

            self._timers.pop(filepath, None)
            self._generations.pop(filepath, None)
            self._discard_handle_locked(filepath)
            log.msg(
                _LOG_PREFIX,
                f"[BatchCache] Auto-closed file handle after timeout: {filepath}",
            )

    def close_all(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._generations.clear()

            for filepath in list(self._handles):
                self._discard_handle_locked(filepath)
                log.debug(_LOG_PREFIX, f"[BatchCache] Closed file handle: {filepath}")

    def set_timeout(self, timeout_seconds: float) -> None:
        with self._lock:
            self._timeout = timeout_seconds


_batch_file_cache = BatchFileCache(timeout_seconds=10.0)


def reset_global_values() -> None:
    global_values.update(
        source_filename="",
        source_folder="",
        source_base_folder="",
        _json_source_filename="",
    )


class FilenameProcessor:
    # Handles filename placeholder processing.

    @staticmethod
    def _now() -> datetime:
        return datetime.now().astimezone()

    @classmethod
    def _placeholder_values(
        cls,
        context: dict[str, str],
        counter: int,
    ) -> dict[str, Callable[[], str]]:
        return {
            "%today": lambda: cls._now().strftime("%Y-%m-%d"),
            "%date": lambda: cls._now().strftime("%Y-%m-%d"),
            "%time": lambda: cls._now().strftime("%H%M%S"),
            "%Y": lambda: cls._now().strftime("%Y"),
            "%y": lambda: cls._now().strftime("%y"),
            "%m": lambda: cls._now().strftime("%m"),
            "%M": lambda: cls._now().strftime("%m"),
            "%d": lambda: cls._now().strftime("%d"),
            "%D": lambda: cls._now().strftime("%d"),
            "%H": lambda: cls._now().strftime("%H"),
            "%S": lambda: cls._now().strftime("%S"),
            "%source_filename": lambda: context.get("source_filename", ""),
            "%source_folder": lambda: context.get("source_folder", ""),
            "%source_base_folder": lambda: context.get("source_base_folder", ""),
            "%counter": lambda: str(counter),
        }

    def get_used_placeholders(self, filename: str) -> list[str]:
        if not isinstance(filename, str):
            log.warning(_LOG_PREFIX, f"Invalid filename type: {type(filename)}")
            return []
        return [
            placeholder
            for placeholder in self._placeholder_values({}, 0)
            if placeholder in filename
        ]

    def get_placeholder_value(
        self,
        placeholder: str,
        context: dict[str, str] | None = None,
        counter: int | None = None,
    ) -> str:
        values = self._placeholder_values(
            global_values if context is None else context,
            _execution_counter if counter is None else counter,
        )
        if placeholder not in values:
            log.debug(
                _LOG_PREFIX,
                f"Unknown placeholder: {placeholder}; falling back to name without %",
            )
            return placeholder.lstrip("%")

        value = values[placeholder]()
        if value in (None, ""):
            log.debug(
                _LOG_PREFIX,
                f"Placeholder {placeholder} resolved to empty; falling back to name without %",
            )
            return placeholder.lstrip("%")
        return value

    def process_string(
        self,
        text: str,
        is_path: bool,
        context: dict[str, str] | None = None,
        counter: int | None = None,
    ) -> str:
        if not text or not isinstance(text, str):
            log.warning(_LOG_PREFIX, "Invalid text for placeholder processing")
            return "default"

        result = text
        for placeholder in self.get_used_placeholders(text):
            result = result.replace(
                placeholder,
                self.get_placeholder_value(placeholder, context, counter),
            )

        if is_path:
            return self._sanitize_path(result)
        return self._sanitize_filename(result)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        invalid_chars = '<>:"/\\|?*' + "".join(chr(i) for i in range(32))
        for char in invalid_chars:
            filename = filename.replace(char, "_")
        filename = filename.strip(" .")
        return filename or "untitled"

    @staticmethod
    def _sanitize_path(path: str) -> str:
        parts = Path(path).parts
        sanitized_parts = []
        for index, part in enumerate(parts):
            if (index == 0 and len(parts) > 1 and part.endswith(":")) or part in {
                ".",
                "..",
            }:
                sanitized_parts.append(part)
                continue

            invalid_chars = '<>:"|?*' + "".join(chr(i) for i in range(32))
            for char in invalid_chars:
                part = part.replace(char, "_")
            sanitized_parts.append(part.strip(" .") or "unnamed")
        return str(Path(*sanitized_parts)) if sanitized_parts else ""


filename_processor = FilenameProcessor()


def string_placeholder(text: str, is_path: bool) -> str:
    # Backward-compatible public interface for placeholder processing.
    return filename_processor.process_string(text, is_path)


def _sanitize_delimiter(delimiter: Any) -> str:
    value = str(delimiter or "")
    invalid_chars = '<>:"/\\|?*' + "".join(chr(i) for i in range(32))
    for char in invalid_chars:
        value = value.replace(char, "_")
    return value.strip() or "_"


def _prepare_text(text: str | None) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError(f"Save Prompt text must be a string, got {type(text).__name__}")
    return re.sub(
        r" +",
        " ",
        text.replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
    ).strip()


def _get_next_counter(
    output_path: Path,
    filename_prefix: str,
    delimiter: str,
    extension: str,
) -> int:
    if not output_path.exists():
        return 1

    pattern = re.compile(
        rf"{re.escape(filename_prefix)}{re.escape(delimiter)}(\d+)\.{re.escape(extension)}"
    )
    counters = [
        int(match.group(1))
        for path in output_path.iterdir()
        if (match := pattern.fullmatch(path.name)) is not None
    ]
    return max(counters, default=0) + 1


def _base_filepath(output_path: Path, filename_prefix: str, extension: str) -> Path:
    return output_path / f"{filename_prefix}.{extension}"


def _save_txt(filepath: Path, text: str, append: bool) -> None:
    mode = "a" if append else "w"
    with filepath.open(mode, encoding="utf-8") as output_file:
        if append and filepath.stat().st_size > 0:
            output_file.write("\n")
        output_file.write(text)


def _save_txt_batch(filepath: Path, text: str) -> None:
    def write_text(handle: TextIO, is_empty: bool) -> None:
        if not is_empty:
            handle.write("\n")
        handle.write(text)

    _batch_file_cache.write(str(filepath), write_text)


def _clean_csv_text(text: str) -> str:
    return re.sub(
        r" +",
        " ",
        text.replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
    ).strip()


def _write_csv_row(
    handle: TextIO,
    text: str,
    positive_name: str,
    negative_prompt: str,
    *,
    include_header: bool,
) -> None:
    if include_header:
        handle.write("name,prompt,negative_prompt\n")
    writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow((positive_name, text, _clean_csv_text(negative_prompt)))


def _save_csv(
    filepath: Path,
    text: str,
    append: bool,
    positive_name: str = "",
    negative_prompt: str = "",
) -> None:
    file_has_content = filepath.exists() and filepath.stat().st_size > 0
    mode = "a" if append else "w"
    with filepath.open(mode, newline="", encoding="utf-8") as output_file:
        _write_csv_row(
            output_file,
            text,
            positive_name,
            negative_prompt,
            include_header=not append or not file_has_content,
        )


def _save_csv_batch(
    filepath: Path,
    text: str,
    positive_name: str = "",
    negative_prompt: str = "",
) -> None:
    def write_row(handle: TextIO, is_empty: bool) -> None:
        _write_csv_row(
            handle,
            text,
            positive_name,
            negative_prompt,
            include_header=is_empty,
        )

    _batch_file_cache.write(str(filepath), write_row, newline="")


def _next_json_entry_key(data: dict[str, Any]) -> str:
    index = 1
    while f"entry_{index}" in data:
        index += 1
    return f"entry_{index}"


def _save_json(
    filepath: Path,
    text: str,
    append: bool,
    source_filename: str = "",
    nsfw_level: str = "",
) -> None:
    entry = {"prompt": text}
    if nsfw_level:
        entry["nsfwLevel"] = nsfw_level

    if append:
        selected_key = ""

        def update(data: dict[str, Any]) -> None:
            nonlocal selected_key
            selected_key = source_filename or _next_json_entry_key(data)
            data[selected_key] = entry

        update_json_object(filepath, update, default={}, indent=4)
        key = selected_key
    else:
        key = source_filename or "entry_1"
        write_json_object(filepath, {key: entry}, indent=4)

    suffix = f" -> nsfwLevel: {nsfw_level}" if nsfw_level else ""
    log.msg(_LOG_PREFIX, f"JSON entry: {key}{suffix}")


def _unwrap_pipe(pipe: Any) -> dict[str, Any]:
    if isinstance(pipe, tuple) and pipe and isinstance(pipe[0], dict):
        return pipe[0]
    if isinstance(pipe, dict):
        return pipe
    return {}


def _sequence_lengths(inputs: dict[str, Any]) -> list[tuple[str, int]]:
    lengths = [
        (name, len(value))
        for name, value in inputs.items()
        if isinstance(value, list) and value
    ]

    pipe_value = inputs.get("pipe_opt")
    pipe_items = pipe_value if isinstance(pipe_value, list) else [pipe_value]
    for pipe_index, pipe_item in enumerate(pipe_items):
        context = _unwrap_pipe(pipe_item)
        for field in ("filepath", "filename", "base_path", "path"):
            value = context.get(field)
            if isinstance(value, list) and value:
                lengths.append((f"pipe_opt[{pipe_index}].{field}", len(value)))
    return lengths


def _validate_and_get_batch_size(inputs: dict[str, Any]) -> int:
    lengths = _sequence_lengths(inputs)
    batch_size = max((length for _, length in lengths), default=1)
    mismatches = [
        f"{name}={length}" for name, length in lengths if length not in {1, batch_size}
    ]
    if mismatches:
        details = ", ".join(mismatches)
        raise ValueError(
            "Save Prompt list inputs must have one item or match the batch size "
            f"{batch_size}; incompatible inputs: {details}"
        )
    return batch_size


def _select_item(value: Any, index: int, batch_size: int, name: str) -> Any:
    if not isinstance(value, list):
        return value
    if not value:
        return None
    if len(value) == 1:
        return value[0]
    if len(value) == batch_size:
        return value[index]
    raise ValueError(
        f"Save Prompt input '{name}' has {len(value)} items; expected 1 or {batch_size}"
    )


@dataclass(frozen=True)
class SourceContext:
    placeholders: dict[str, str]
    source_folder: Path | None
    base_folder: Path | None


def _build_source_context(
    pipe: Any,
    filename: Any,
    index: int,
    batch_size: int,
) -> SourceContext:
    pipe_context = _unwrap_pipe(pipe)
    filepath = _select_item(
        pipe_context.get("filepath") or pipe_context.get("filename") or "",
        index,
        batch_size,
        "pipe_opt.filepath",
    )
    base_folder_value = _select_item(
        pipe_context.get("base_path") or pipe_context.get("path") or "",
        index,
        batch_size,
        "pipe_opt.base_path",
    )

    filepath = str(filepath or filename or "")
    base_folder_value = str(base_folder_value or "")
    source_folder = Path(filepath).parent if filepath else None
    base_folder = Path(base_folder_value) if base_folder_value else source_folder
    source_folder_name = source_folder.name if source_folder else ""
    source_base_name = base_folder.name if base_folder else source_folder_name
    basename = Path(filepath).name if filepath else ""

    return SourceContext(
        placeholders={
            "source_filename": Path(basename).stem if basename else "",
            "source_folder": source_folder_name,
            "source_base_folder": source_base_name,
            "_json_source_filename": basename,
        },
        source_folder=source_folder,
        base_folder=base_folder,
    )


def _starts_with_folder(path: str, folder_name: str) -> bool:
    normalized = path.replace("\\", "/")
    return bool(folder_name) and (
        normalized == folder_name or normalized.startswith(f"{folder_name}/")
    )


def _strip_folder_prefix(path: str, folder_name: str) -> tuple[str, bool]:
    normalized = path.replace("\\", "/")
    if not folder_name:
        return path, False
    if normalized == folder_name:
        return "", True
    if normalized.startswith(f"{folder_name}/"):
        return path[len(folder_name) + 1 :], True
    return path, False


def _resolve_output_path(
    raw_output_path: Any,
    use_source_folder: bool,
    source: SourceContext,
    counter: int,
) -> Path:
    raw_value = str(raw_output_path or "")
    processed = (
        filename_processor.process_string(
            raw_value,
            True,
            source.placeholders,
            counter,
        )
        if raw_value
        else raw_value
    )

    if processed not in _EMPTY_PATHS and os.path.isabs(processed):
        return Path(processed).resolve(strict=False)

    source_folder = source.source_folder
    source_base_name = source.placeholders["source_base_folder"]
    source_folder_name = source.placeholders["source_folder"]
    source_anchored = source_folder is not None and (
        use_source_folder
        or _starts_with_folder(processed, source_base_name)
        or _starts_with_folder(processed, source_folder_name)
    )

    if source_anchored and source_folder is not None:
        if processed in _EMPTY_PATHS:
            return source_folder.resolve(strict=False)

        if (
            processed.startswith("./") and not processed.startswith("../")
        ) or (
            processed.startswith(".\\") and not processed.startswith("..\\")
        ):
            processed = f"..{processed[1:]}"

        processed, used_base = _strip_folder_prefix(processed, source_base_name)
        if used_base:
            join_base = source.base_folder or source_folder
        else:
            processed, _ = _strip_folder_prefix(processed, source_folder_name)
            join_base = source_folder
        return (join_base / processed).resolve(strict=False)

    output_root = Path(folder_paths.get_output_directory()).resolve(strict=False)
    if processed in _EMPTY_PATHS:
        return output_root

    relative_path = FilenameProcessor._sanitize_path(processed)
    destination = (output_root / relative_path).resolve(strict=False)
    try:
        destination.relative_to(output_root)
    except ValueError as error:
        raise ValueError(
            "Save Prompt output_path escapes the ComfyUI output directory without "
            f"source context: {raw_value}"
        ) from error
    return destination


@dataclass(frozen=True)
class SaveRequest:
    original_text: str | None
    clean_text: str
    output_path: Path
    filename_prefix: str
    delimiter: str
    padding: int
    extension: str
    write_mode: str
    csv_positive_name: str
    csv_negative_prompt: str
    nsfw_level: str
    log_prompt: bool
    json_source_filename: str


class RvText_SavePrompt(io.ComfyNode):
    # Save text/prompt to a file in txt, csv, or json format.

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Save Prompt [Eclipse]",
            display_name="Save Prompt",
            category=CATEGORY.MAIN.value + CATEGORY.TEXT.value,
            is_output_node=True,
            is_input_list=True,
            inputs=[
                io.String.Input(
                    "text", force_input=True, tooltip="The text/prompt to save to file."
                ),
                io.String.Input(
                    "output_path",
                    default="",
                    tooltip="Output folder path. Leave empty and enable use_source_folder to save alongside source images. Supports placeholders: %source_folder, %source_base_folder, %counter, %date, %time.",
                ),
                io.Boolean.Input(
                    "use_source_folder",
                    default=True,
                    tooltip="When enabled, resolves output_path relative to the source image folder.",
                ),
                io.String.Input(
                    "filename_prefix",
                    default="%source_filename",
                    tooltip="Prefix for the filename. Supports placeholders: %source_filename (recommended for batch captioning), %source_folder, %source_base_folder, %counter, %date, %time, etc.",
                ),
                io.String.Input(
                    "filename_delimiter",
                    default="_",
                    tooltip="Delimiter between filename parts.",
                ),
                io.Int.Input(
                    "filename_number_padding",
                    default=4,
                    min=1,
                    max=9,
                    step=1,
                    tooltip="Number of digits for the counter (e.g., 4 = 0001). Only used in 'new' mode.",
                ),
                io.Combo.Input(
                    "extension",
                    options=["txt", "csv", "json"],
                    default="txt",
                    tooltip="File format: txt (plain text), csv (name,prompt,negative_prompt), json.",
                ),
                io.Combo.Input(
                    "write_mode",
                    options=["new", "overwrite", "append", "append_batch", "keep"],
                    default="new",
                    tooltip="new: numbered files (prefix_0001.txt), overwrite: overwrites existing file, append: adds text to file (opens/closes each time), append_batch: keeps file open for fast batch processing (auto-closes after 10s idle), keep: skip if file exists.",
                ),
                io.String.Input(
                    "csv_positive_name",
                    default="\u2705Style",
                    tooltip="[CSV] Name/label for the style entry (e.g., '\u2705Line Art / Manga').",
                ),
                io.String.Input(
                    "csv_negative_prompt",
                    default="ugly, deformed, noisy, low poly, blurry, painting",
                    multiline=True,
                    tooltip="[CSV] Negative prompt text for the style.",
                ),
                io.Combo.Input(
                    "nsfw_level",
                    options=["disabled", "auto", "None", "Mature", "X"],
                    default="disabled",
                    tooltip="[JSON only] NSFW level tagging. 'auto' detects from text keywords.",
                ),
                io.Boolean.Input(
                    "log_prompt",
                    default=False,
                    label_on="yes",
                    label_off="no",
                    tooltip="Log the saved prompt to console.",
                ),
                io.String.Input(
                    "filename_opt",
                    default=None,
                    force_input=True,
                    optional=True,
                    tooltip="Optional: Full filepath to source file. Enables source placeholders without needing a pipe.",
                ),
                io.Custom("PIPE").Input(
                    "pipe_opt",
                    optional=True,
                    tooltip="Optional Load Image From Folder pipe. Overrides filename_opt when it provides source information.",
                ),
            ],
            outputs=[io.String.Output("text", is_output_list=True)],
        )

    @classmethod
    def execute(
        cls,
        text,
        output_path,
        use_source_folder,
        filename_prefix,
        filename_delimiter,
        filename_number_padding,
        extension,
        write_mode,
        csv_positive_name="\u2705Style",
        csv_negative_prompt="",
        nsfw_level="disabled",
        log_prompt=False,
        filename_opt=None,
        pipe_opt=None,
    ):
        inputs = {
            "text": text,
            "output_path": output_path,
            "use_source_folder": use_source_folder,
            "filename_prefix": filename_prefix,
            "filename_delimiter": filename_delimiter,
            "filename_number_padding": filename_number_padding,
            "extension": extension,
            "write_mode": write_mode,
            "csv_positive_name": csv_positive_name,
            "csv_negative_prompt": csv_negative_prompt,
            "nsfw_level": nsfw_level,
            "log_prompt": log_prompt,
            "filename_opt": filename_opt,
            "pipe_opt": pipe_opt,
        }
        batch_size = _validate_and_get_batch_size(inputs)
        requests: list[SaveRequest] = []

        # Prepare and validate the entire batch before creating directories or files.
        for index in range(batch_size):
            values = {
                name: _select_item(value, index, batch_size, name)
                for name, value in inputs.items()
            }
            source = _build_source_context(
                values["pipe_opt"],
                values["filename_opt"],
                index,
                batch_size,
            )
            counter = _next_execution_counter()
            clean_text = _prepare_text(values["text"])
            selected_extension = str(values["extension"])
            selected_mode = str(values["write_mode"])
            if selected_extension not in {"txt", "csv", "json"}:
                raise ValueError(f"Unsupported Save Prompt extension: {selected_extension}")
            if selected_mode not in {
                "new",
                "overwrite",
                "append",
                "append_batch",
                "keep",
            }:
                raise ValueError(f"Unsupported Save Prompt write mode: {selected_mode}")

            prefix = filename_processor.process_string(
                str(values["filename_prefix"] or ""),
                False,
                source.placeholders,
                counter,
            )
            prefix = FilenameProcessor._sanitize_filename(prefix)
            if prefix in {"", "untitled"} and source.placeholders["source_filename"]:
                prefix = source.placeholders["source_filename"]

            selected_nsfw_level = ""
            if selected_extension == "json" and values["nsfw_level"] != "disabled":
                selected_nsfw_level = (
                    detect_nsfw_level(clean_text)
                    if values["nsfw_level"] == "auto"
                    else str(values["nsfw_level"])
                )

            requests.append(
                SaveRequest(
                    original_text=values["text"],
                    clean_text=clean_text,
                    output_path=_resolve_output_path(
                        values["output_path"],
                        bool(values["use_source_folder"]),
                        source,
                        counter,
                    ),
                    filename_prefix=prefix,
                    delimiter=_sanitize_delimiter(values["filename_delimiter"]),
                    padding=int(values["filename_number_padding"]),
                    extension=selected_extension,
                    write_mode=selected_mode,
                    csv_positive_name=str(values["csv_positive_name"] or ""),
                    csv_negative_prompt=str(values["csv_negative_prompt"] or ""),
                    nsfw_level=selected_nsfw_level,
                    log_prompt=bool(values["log_prompt"]),
                    json_source_filename=source.placeholders[
                        "_json_source_filename"
                    ],
                )
            )

        for request in requests:
            if not request.clean_text:
                log.debug(_LOG_PREFIX, "Skipping save - input text is empty")
                continue

            try:
                request.output_path.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                log.error(
                    _LOG_PREFIX,
                    f"Unable to create output directory: {request.output_path}: {error}",
                )
                raise RuntimeError(
                    f"Save Prompt could not create output directory '{request.output_path}'"
                ) from error

            filepath = _base_filepath(
                request.output_path,
                request.filename_prefix,
                request.extension,
            )
            append = False
            use_batch = request.write_mode == "append_batch"

            if request.write_mode == "new" and filepath.exists():
                number = _get_next_counter(
                    request.output_path,
                    request.filename_prefix,
                    request.delimiter,
                    request.extension,
                )
                filepath = request.output_path / (
                    f"{request.filename_prefix}{request.delimiter}"
                    f"{number:0{request.padding}}.{request.extension}"
                )
            elif request.write_mode == "keep" and filepath.exists():
                log.msg(
                    _LOG_PREFIX,
                    f"File already exists, skipping (keep mode): {filepath}",
                )
                if request.log_prompt:
                    log.msg(_LOG_PREFIX, f"Filepath: {filepath}")
                    log.msg(_LOG_PREFIX, f"Prompt: {request.clean_text}")
                    if request.csv_negative_prompt:
                        log.msg(
                            _LOG_PREFIX,
                            f"Negative prompt: {request.csv_negative_prompt}",
                        )
                continue
            elif request.write_mode in {"append", "append_batch"}:
                append = True

            try:
                if request.extension == "txt":
                    if use_batch:
                        _save_txt_batch(filepath, request.clean_text)
                    else:
                        _save_txt(filepath, request.clean_text, append)
                elif request.extension == "csv":
                    if use_batch:
                        _save_csv_batch(
                            filepath,
                            request.clean_text,
                            request.csv_positive_name,
                            request.csv_negative_prompt,
                        )
                    else:
                        _save_csv(
                            filepath,
                            request.clean_text,
                            append,
                            request.csv_positive_name,
                            request.csv_negative_prompt,
                        )
                else:
                    _save_json(
                        filepath,
                        request.clean_text,
                        append,
                        request.json_source_filename,
                        request.nsfw_level,
                    )
            except (OSError, JsonStoreError, csv.Error) as error:
                log.error(_LOG_PREFIX, f"Unable to save file '{filepath}': {error}")
                raise RuntimeError(
                    f"Save Prompt could not save '{filepath}': {error}"
                ) from error

            if not use_batch:
                log.msg(_LOG_PREFIX, f"Prompt saved to: {filepath}")
            if request.log_prompt:
                log.msg(_LOG_PREFIX, f"Filepath: {filepath}")
                log.msg(_LOG_PREFIX, f"Prompt: {request.clean_text}")
                if request.csv_negative_prompt:
                    log.msg(
                        _LOG_PREFIX,
                        f"Negative prompt: {request.csv_negative_prompt}",
                    )

        return io.NodeOutput([request.original_text for request in requests])
