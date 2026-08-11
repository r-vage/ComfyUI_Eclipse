# Live terminal progress for model acquisition phases.

from __future__ import annotations

import sys
import threading
from typing import TextIO

_PROGRESS_PHASE_LABELS = {
    "transferring": "Downloading",
    "hashing": "Hashing",
    "verifying": "Verifying",
}
_UNKNOWN_TOTAL_STEP = 64 * 1024 * 1024


class ConsolePhaseProgress:
    def __init__(self, prefix: str, *, stream: TextIO | None = None) -> None:
        self._prefix = prefix
        self._stream = stream
        self._lock = threading.Lock()
        self._phase: str | None = None
        self._last_key: tuple[str, int] | None = None
        self._line_open = False
        self._line_width = 0

    def _finish_open_line(self, stream: TextIO) -> None:
        if not self._line_open:
            return
        stream.write("\n")
        stream.flush()
        self._line_open = False
        self._line_width = 0

    def update(
        self,
        filename: str,
        phase: str,
        processed: int = 0,
        total: int = 0,
        *,
        terminal: bool = False,
    ) -> None:
        safe_processed = max(0, int(processed))
        safe_total = max(0, int(total))
        stream = self._stream or sys.stdout
        with self._lock:
            if phase != self._phase:
                self._finish_open_line(stream)
                self._phase = phase
                self._last_key = None

            progress_label = _PROGRESS_PHASE_LABELS.get(phase)
            if progress_label is None:
                key = (phase, 0)
                if key == self._last_key:
                    return
                self._last_key = key
                stream.write(f"Eclipse: [{self._prefix}] {filename}: {phase}\n")
                stream.flush()
                return

            if safe_total:
                percent = min(100, int(safe_processed / safe_total * 100))
                key = (phase, percent)
                progress_text = (
                    f"{percent}% "
                    f"({safe_processed / (1024 * 1024):.0f}/"
                    f"{safe_total / (1024 * 1024):.0f} MB)"
                )
            else:
                key = (phase, safe_processed // _UNKNOWN_TOTAL_STEP)
                progress_text = f"{safe_processed / (1024 * 1024):.0f} MB"
            if key == self._last_key and not terminal:
                return
            self._last_key = key

            message = (
                f"Eclipse: [{self._prefix}]   {progress_label} {filename}: "
                f"{progress_text}"
            )
            padding = " " * max(0, self._line_width - len(message))
            stream.write(f"\r{message}{padding}")
            stream.flush()
            self._line_open = True
            self._line_width = len(message)
            if terminal:
                self._finish_open_line(stream)

    def finish(self) -> None:
        stream = self._stream or sys.stdout
        with self._lock:
            self._finish_open_line(stream)
