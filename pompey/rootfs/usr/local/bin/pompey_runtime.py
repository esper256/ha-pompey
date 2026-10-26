"""Small shared process, locking and state primitives; no runtime package manager."""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess

HEALTH = {
    "radarr": "http://127.0.0.1:7878/ping",
    "sonarr": "http://127.0.0.1:8989/ping",
    "prowlarr": "http://127.0.0.1:9696/ping",
    "qbittorrent": "http://127.0.0.1:8080/api/v2/app/version",
    "seerr": "http://127.0.0.1:5055/api/v1/settings/public",
}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as out:
        json.dump(data, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def stack_lock(blocking=True):
    path = Path(os.environ.get("POMPEY_DATA", "/data/pompey")) / "stack.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def run_process(args, timeout):
    """Finish or stop the whole mutation job before its caller releases the lock."""
    proc = subprocess.Popen(args, start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, args)
    finally:
        # Also clean up children whose parent exited successfully or was killed.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
