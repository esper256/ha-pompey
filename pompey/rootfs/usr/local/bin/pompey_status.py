#!/usr/bin/env python3
"""Initial setup status. Once running, the controller owns live status."""

import fcntl
import os
from pathlib import Path
import sys
import time
from pompey_runtime import atomic_json

READY = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
STATUS_PATH = Path(os.environ.get("POMPEY_STATUS", str(READY / "status.json")))


def lock_status():
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STATUS_PATH) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def default_status(*, search=False):
    return {
        "updated": time.time(),
        "step": "start",
        "label": "Starting applications",
        "percent": 20,
        "error": "",
        "need_vpn": False,
        "search": search,
        "search_port": int(os.environ.get("SEERR_PORT", "5055")),
        "sources_port": int(os.environ.get("PROWLARR_PORT", "9696")),
        "steps": [
            {"id": name, "label": label, "state": "pending"}
            for name, label in [
                ("start", "Start applications"),
                ("wire", "Configure library connections"),
                ("ready", "Ready"),
            ]
        ],
    }


def main():
    if (READY / "health.json").exists():
        return
    data = default_status()
    data.update(
        step=sys.argv[1],
        label=sys.argv[2],
        percent=int(sys.argv[3]) if len(sys.argv) > 3 else 20,
        error=" ".join(sys.argv[4:]),
        need_vpn=os.environ.get("POMPEY_STATUS_NEED_VPN") == "1",
    )
    lock = lock_status()
    try:
        atomic_json(STATUS_PATH, data)
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
