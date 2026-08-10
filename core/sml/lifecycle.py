# Shared exception-safe execution cleanup for Smart LM nodes.

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from .logger import log

_CleanupCallback = Callable[[], None]
_execution_cleanups: ContextVar[list[_CleanupCallback] | None] = ContextVar(
    "sml_execution_cleanups",
    default=None,
)
_activity_condition = threading.Condition()
_active_executions = 0
_model_maintenance_active = False


def _begin_execution_activity() -> None:
    # A short destructive maintenance operation may already own the gate.
    global _active_executions
    with _activity_condition:
        while _model_maintenance_active:
            _activity_condition.wait()
        _active_executions += 1


def _end_execution_activity() -> None:
    global _active_executions
    with _activity_condition:
        if _active_executions <= 0:
            raise RuntimeError("Smart LM execution activity is unbalanced")
        _active_executions -= 1
        if _active_executions == 0:
            _activity_condition.notify_all()


@contextmanager
def model_maintenance_if_idle() -> Iterator[bool]:
    # Reserve destructive model maintenance only when no SML node is active.
    # Endpoint callers can fail fast instead of blocking ComfyUI's event loop.
    global _model_maintenance_active
    acquired = False
    with _activity_condition:
        if not _model_maintenance_active and _active_executions == 0:
            _model_maintenance_active = True
            acquired = True
    try:
        yield acquired
    finally:
        if acquired:
            with _activity_condition:
                _model_maintenance_active = False
                _activity_condition.notify_all()


def register_execution_cleanup(callback: _CleanupCallback) -> None:
    # Register cleanup for the active decorated node execution.
    callbacks = _execution_cleanups.get()
    if callbacks is None:
        raise RuntimeError("No Smart LM execution cleanup scope is active")
    callbacks.append(callback)


def _run_execution_cleanups(
    callbacks: list[_CleanupCallback],
    log_prefix: str,
    suppress_errors: bool,
) -> None:
    # Run every cleanup in reverse registration order.
    first_error = None
    for callback in reversed(callbacks):
        try:
            callback()
        except Exception as error:  # noqa: BLE001 - cleanup must not skip callbacks
            log.error(
                log_prefix,
                f"Execution cleanup failed: {type(error).__name__}: {error}",
            )
            if first_error is None:
                first_error = error

    if first_error is not None and not suppress_errors:
        raise first_error


def with_execution_cleanup(log_prefix: str):
    # Decorate a synchronous node execution with isolated cleanup callbacks.
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            _begin_execution_activity()
            try:
                callbacks: list[_CleanupCallback] = []
                token = _execution_cleanups.set(callbacks)
                try:
                    try:
                        result = function(*args, **kwargs)
                    except BaseException:
                        _run_execution_cleanups(
                            callbacks,
                            log_prefix,
                            suppress_errors=True,
                        )
                        raise
                    else:
                        _run_execution_cleanups(
                            callbacks,
                            log_prefix,
                            suppress_errors=False,
                        )
                        return result
                finally:
                    _execution_cleanups.reset(token)
            finally:
                _end_execution_activity()

        return wrapper

    return decorator
