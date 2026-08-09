#!/usr/bin/env python3
"""Portable per-file locking for Eyle persistence.

Eyle may persist the same JSON file from the web process and isolated worker
processes. A process-local ``threading.Lock`` prevents thread races but cannot
prevent lost updates across ``multiprocessing.Process`` boundaries. ``lock_para``
therefore combines one in-process mutex per normalized path with an OS-backed
advisory lock on a stable sidecar file.

The sidecar may remain on disk after use; the operating-system lock, not file
existence, is the authority. Crashed processes release the OS lock automatically.
"""
from __future__ import annotations

import os
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


def _normalized_path(caminho):
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(caminho))))


class _InterProcessFileLock:
    def __init__(self, caminho):
        self.path = _normalized_path(caminho)
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
            elif msvcrt is not None:  # pragma: no cover - exercised on Windows
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - unsupported interpreter/platform
                raise RuntimeError("interprocess file locking is unavailable on this platform")
        except BaseException:
            handle.close()
            raise
        self._handle = handle

    def _release_os_lock(self):
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - exercised on Windows
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


def lock_para(caminho):
    """Return a context manager serializing one path across threads/processes."""
    return _InterProcessFileLock(caminho)
