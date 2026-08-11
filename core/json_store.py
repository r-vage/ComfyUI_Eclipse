# Durable, locked, atomic JSON-object persistence shared by Eclipse subsystems.

import copy
import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class JsonStoreError(RuntimeError):
    pass


JsonObject = dict[str, Any]
JsonObjectUpdater = Callable[[JsonObject], None]

_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_LOCK_DIRECTORY_NAME = ".locks"


def _canonical_lock_key(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=False)
    return os.path.normcase(str(resolved))


def _get_thread_lock(path: Path) -> threading.RLock:
    lock_key = _canonical_lock_key(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(lock_key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[lock_key] = lock
        return lock


def _lock_path(path: Path) -> Path:
    lock_directory = path.parent / _LOCK_DIRECTORY_NAME
    if lock_directory.is_symlink():
        raise JsonStoreError(
            f"JSON lock directory may not be a symlink: {lock_directory}"
        )
    try:
        lock_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise JsonStoreError(
            f"Could not create JSON lock directory '{lock_directory}': {error}"
        ) from error
    if lock_directory.is_symlink() or not lock_directory.is_dir():
        raise JsonStoreError(
            f"JSON lock directory must be a real directory: {lock_directory}"
        )
    if os.name != "nt":
        try:
            lock_directory.chmod(0o700)
        except OSError as error:
            raise JsonStoreError(
                f"Could not secure JSON lock directory '{lock_directory}': {error}"
            ) from error
    return lock_directory / f"{path.name}.lock"


@contextmanager
def _interprocess_lock(path: Path) -> Iterator[None]:
    # A persistent lock inode prevents os.replace() from splitting lock domains.
    lock_path = _lock_path(path)
    if lock_path.is_symlink():
        raise JsonStoreError(f"JSON lock path may not be a symlink: {lock_path}")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise JsonStoreError(f"Could not open JSON lock '{lock_path}': {error}") from error
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _locked_json_path(path: Path) -> Iterator[None]:
    if not path.parent.is_dir():
        raise JsonStoreError(f"JSON parent directory does not exist: {path.parent}")
    with _get_thread_lock(path), _interprocess_lock(path):
        yield


def _read_json_object_unlocked(path: Path, default: JsonObject | None) -> JsonObject:
    try:
        with path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except FileNotFoundError:
        data = {} if default is None else copy.deepcopy(default)
    except (OSError, json.JSONDecodeError) as error:
        raise JsonStoreError(f"Could not read JSON object '{path}': {error}") from error

    if not isinstance(data, dict):
        raise JsonStoreError(f"JSON root must be an object: {path}")
    return data


def _fsync_directory(directory: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _destination_mode(path: Path, private: bool) -> int:
    if private:
        return 0o600
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return 0o600


def _atomic_write_json_unlocked(
    path: Path,
    data: JsonObject,
    *,
    private: bool,
    indent: int,
    ensure_ascii: bool,
) -> None:
    try:
        serialized = json.dumps(
            data,
            allow_nan=False,
            ensure_ascii=ensure_ascii,
            indent=indent,
        )
    except (TypeError, ValueError) as error:
        raise JsonStoreError(f"JSON object for '{path}' is not serializable: {error}") from error

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        mode = _destination_mode(path, private)
        try:
            os.fchmod(descriptor, mode)
        except (AttributeError, OSError):
            if os.name != "nt":
                raise

        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            descriptor = -1
            temporary_file.write(serialized)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json_object(
    path: str | os.PathLike[str],
    *,
    default: JsonObject | None = None,
) -> JsonObject:
    target = Path(path)
    with _locked_json_path(target):
        return copy.deepcopy(_read_json_object_unlocked(target, default))


@contextmanager
def locked_path(path: str | os.PathLike[str]) -> Iterator[None]:
    with _locked_json_path(Path(path)):
        yield


def update_json_object(
    path: str | os.PathLike[str],
    updater: JsonObjectUpdater,
    *,
    default: JsonObject | None = None,
    private: bool = False,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> JsonObject:
    target = Path(path)
    with _locked_json_path(target):
        target_exists = target.exists()
        current = _read_json_object_unlocked(target, default)
        working = copy.deepcopy(current)
        updated = updater(working)
        if updated is not None:
            raise JsonStoreError("JSON updater must mutate the object and return None")
        if not target_exists or working != current:
            _atomic_write_json_unlocked(
                target,
                working,
                private=private,
                indent=indent,
                ensure_ascii=ensure_ascii,
            )
        elif private:
            try:
                target.chmod(0o600)
            except (FileNotFoundError, OSError):
                if os.name != "nt":
                    raise
        return copy.deepcopy(working)


def write_json_object(
    path: str | os.PathLike[str],
    data: JsonObject,
    *,
    private: bool = False,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> JsonObject:
    if not isinstance(data, dict):
        raise JsonStoreError("JSON root must be an object")
    replacement = copy.deepcopy(data)
    target = Path(path)
    with _locked_json_path(target):
        _atomic_write_json_unlocked(
            target,
            replacement,
            private=private,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )
        return copy.deepcopy(replacement)
