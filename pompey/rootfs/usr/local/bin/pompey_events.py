"""Opt-in acceptance evidence at failure sources; no journal in normal installs."""

import fcntl
import json
import os
from pathlib import Path
import time


def record(kind, **details):
    target = os.environ.get("POMPEY_EVENT_LOG")
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(json.dumps({"time": time.time(), "kind": kind, **details}) + "\n")
        output.flush()
        os.fsync(output.fileno())


def policy_attention():
    if not os.environ.get("POMPEY_EVENT_LOG"):
        return
    from media_policy import attention

    notices = attention()
    if notices:
        record("attention", notices=notices)
