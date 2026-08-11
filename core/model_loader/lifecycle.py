# Concurrency gate for diffusion-loader execution and destructive maintenance.

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps

_CONDITION = threading.Condition()
_ACTIVE_LOADS = 0
_MAINTENANCE_ACTIVE = False


@contextmanager
def loader_execution() -> Iterator[None]:
    global _ACTIVE_LOADS
    with _CONDITION:
        while _MAINTENANCE_ACTIVE:
            _CONDITION.wait()
        _ACTIVE_LOADS += 1
    try:
        yield
    finally:
        with _CONDITION:
            _ACTIVE_LOADS -= 1
            if _ACTIVE_LOADS == 0:
                _CONDITION.notify_all()


def with_loader_execution(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with loader_execution():
            return function(*args, **kwargs)

    return wrapped


def _resolve_prompt_queue(prompt_queue=None):
    if prompt_queue is None:
        try:
            from server import PromptServer  # type: ignore

            prompt_queue = getattr(PromptServer.instance, "prompt_queue", None)
        except (AttributeError, ImportError):
            prompt_queue = None
    return prompt_queue


def _prompt_queue_state_is_idle(prompt_queue) -> bool:
    if prompt_queue is None:
        return False

    running = getattr(prompt_queue, "currently_running", None)
    pending = getattr(prompt_queue, "queue", None)
    if running is not None or pending is not None:
        return not running and not pending
    get_current_queue = getattr(prompt_queue, "get_current_queue", None)
    if callable(get_current_queue):
        current = get_current_queue()
        return bool(
            isinstance(current, tuple)
            and len(current) >= 2
            and not current[0]
            and not current[1]
        )
    return False


def prompt_queue_is_idle(prompt_queue=None) -> bool:
    prompt_queue = _resolve_prompt_queue(prompt_queue)
    if prompt_queue is None:
        return False
    queue_mutex = getattr(prompt_queue, "mutex", None)
    if queue_mutex is None:
        return _prompt_queue_state_is_idle(prompt_queue)
    with queue_mutex:
        return _prompt_queue_state_is_idle(prompt_queue)


@contextmanager
def maintenance_if_idle(prompt_queue=None) -> Iterator[bool]:
    global _MAINTENANCE_ACTIVE
    prompt_queue = _resolve_prompt_queue(prompt_queue)
    queue_mutex = getattr(prompt_queue, "mutex", None)
    queue_mutex_acquired = False
    acquired = False
    try:
        if queue_mutex is not None:
            queue_mutex.acquire()
            queue_mutex_acquired = True

        with _CONDITION:
            if (
                not _MAINTENANCE_ACTIVE
                and _ACTIVE_LOADS == 0
                and _prompt_queue_state_is_idle(prompt_queue)
            ):
                _MAINTENANCE_ACTIVE = True
                acquired = True
        if not acquired and queue_mutex_acquired:
            queue_mutex.release()
            queue_mutex_acquired = False
        yield acquired
    finally:
        if acquired:
            with _CONDITION:
                _MAINTENANCE_ACTIVE = False
                _CONDITION.notify_all()
        if queue_mutex_acquired:
            queue_mutex.release()
