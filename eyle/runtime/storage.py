#!/usr/bin/env python3
"""Atomic JSON persistence and cross-process file locking."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from collections import defaultdict

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _normalized_path(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


class _InterProcessFileLock:
    def __init__(self, path):
        self.path = _normalized_path(path)
        with _locks_guard:
            self._thread_lock = _locks[self.path]
        self._handle = None

    @property
    def lock_path(self):
        return self.path + ".lock"

    def _acquire_os_lock(self):
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover
                raise RuntimeError("interprocess file locking is unavailable on this platform")
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def _release_os_lock(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            self._acquire_os_lock()
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._release_os_lock()
        finally:
            self._thread_lock.release()
        return False


def lock_para(path):
    return _InterProcessFileLock(path)


def _publish_atomic(path, writer):
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    previous_mode = None
    try:
        previous_mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        pass

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def salvar_json_atomico(path, data, *, indent=2):
    """Publish one complete JSON document without exposing partial writes."""
    _publish_atomic(
        path,
        lambda handle: json.dump(data, handle, ensure_ascii=False, indent=indent),
    )
