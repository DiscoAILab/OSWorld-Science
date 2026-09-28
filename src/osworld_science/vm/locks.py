"""One VM, one run. A port lock is held for the whole job; a second process
asking for the same port fails immediately instead of resetting a guest that
another sweep is using (that produced false zeros before)."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class PortBusy(RuntimeError):
    pass


class PortLock:
    def __init__(self, lock_dir: Path, port: int):
        self.path = Path(lock_dir) / f"{int(port)}.lock"
        self.port = int(port)
        self._fh = None

    def __enter__(self) -> PortLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.seek(0)
            holder = self._fh.read().strip()
            self._fh.close()
            self._fh = None
            raise PortBusy(f"port {self.port} is locked by another run ({holder or 'unknown'}); "
                           f"lock file {self.path}") from None
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"pid={os.getpid()}\n")
        self._fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


def port_lock_held(lock_dir: Path, port: int) -> bool:
    """True when another process holds the lock for `port` (probe, never blocks)."""
    path = Path(lock_dir) / f"{int(port)}.lock"
    if not path.exists():
        return False
    with open(path, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False

