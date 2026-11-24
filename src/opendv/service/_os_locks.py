"""In-process reentrant and cross-process OS file locking for owner-only state files."""

from __future__ import annotations

import errno
import os
import random
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.service._os_acl import set_owner_only_permissions, verify_owner_only_permissions

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class _PathLockEntry:
    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, _PathLockEntry] = {}


def _canonical_lock_key(lock_path: Path) -> str:
    parent_real = os.path.realpath(os.fspath(lock_path.parent))
    if sys.platform == "win32":
        if parent_real.startswith("\\\\?\\UNC\\"):
            parent_real = "\\\\" + parent_real[8:]
        elif parent_real.startswith("\\\\?\\"):
            parent_real = parent_real[4:]
    return os.path.normcase(os.path.join(os.path.abspath(parent_real), lock_path.name))


def _get_path_lock_entry(lock_path: Path) -> _PathLockEntry:
    key = _canonical_lock_key(lock_path)
    with _PATH_LOCKS_GUARD:
        entry = _PATH_LOCKS.get(key)
        if entry is None:
            entry = _PathLockEntry()
            _PATH_LOCKS[key] = entry
        return entry


@contextmanager
def locked_file_access(
    lock_path: Path,
    *,
    owner_only: bool = True,
    timeout: float = 15.0,
) -> Iterator[None]:
    """Acquire an in-process reentrant RLock and cross-process OS file lock on lock_path."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    entry = _get_path_lock_entry(lock_path)
    with entry.rlock:
        if entry.depth > 0:
            entry.depth += 1
            try:
                yield
            finally:
                entry.depth -= 1
            return

        if owner_only and not verify_owner_only_permissions(lock_path.parent):
            set_owner_only_permissions(lock_path.parent)

        fd: int | None = None
        locked = False
        deadline = time.monotonic() + timeout
        delay = 0.005
        try:
            while True:
                try:
                    fd = os.open(os.fspath(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
                except OSError as exc:
                    winerror = getattr(exc, "winerror", None)
                    if (
                        sys.platform == "win32"
                        and winerror in (5, 32)
                        and time.monotonic() < deadline
                    ):
                        time.sleep(delay + random.uniform(0.001, 0.005))
                        delay = min(delay * 1.5, 0.05)
                        continue
                    raise OpenDVError(
                        f"Failed to open lock file '{lock_path.name}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc

                try:
                    if sys.platform == "win32":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError as exc:
                    os.close(fd)
                    fd = None
                    busy_errnos = (errno.EACCES, errno.EAGAIN, errno.EDEADLK, errno.EINTR)
                    winerror = getattr(exc, "winerror", None)
                    if (exc.errno in busy_errnos or winerror in (33, 32, 5)) and (
                        time.monotonic() < deadline
                    ):
                        time.sleep(delay + random.uniform(0.001, 0.005))
                        delay = min(delay * 1.5, 0.05)
                        continue
                    raise OpenDVError(
                        f"Timed out acquiring lock '{lock_path.name}': {exc}",
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    ) from exc

            if owner_only and not verify_owner_only_permissions(lock_path):
                set_owner_only_permissions(lock_path)

            entry.depth = 1
            yield
        finally:
            if entry.depth > 0:
                entry.depth -= 1
            if locked and fd is not None:
                with suppress(OSError):
                    if sys.platform == "win32":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)


__all__ = ["locked_file_access"]
