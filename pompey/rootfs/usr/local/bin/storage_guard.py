"""Bounded storage observation and conservative service hold after detected loss."""

import json
from pathlib import Path
import subprocess
import sys
import time
from pompey_runtime import atomic_json, stack_lock
from pompey_log_emit import emit

SERVICES = ("qbittorrent", "radarr", "sonarr")


class StorageGuard:
    def __init__(self, ready):
        self.ready = ready
        self.future = None
        self.due = 0
        path = ready / "storage.json"
        self.status = json.loads(path.read_text()) if path.exists() else {"ok": False, "phase": "checking"}
        self.blocked = not self.status.get("ok", False)
        self.last_notice = None

    def notice(self, level, message):
        # Record transitions, not every 15-second probe of the same outage.
        if message != self.last_notice:
            emit("storage", f"[{level}] {message}")
            self.last_notice = message

    def save(self, **values):
        self.status = {**self.status, **values, "updated": time.time()}
        atomic_json(self.ready / "storage.json", self.status)

    def check(self):
        try:
            result = subprocess.run([sys.executable, "/usr/local/bin/pompey_storage.py", "report"], capture_output=True, text=True, timeout=8, check=True)
            current = json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            current = {"ok": False, "reason": "Storage check exceeded 8 seconds. Check the share and server connection."}
        except (OSError, ValueError, subprocess.SubprocessError):
            current = {"ok": False, "reason": "Storage check did not complete. Check the share and server connection."}
        if not current["ok"]:
            self.blocked = True
            self.save(**current, phase="stopping")
            self.notice("Warning", f"Pausing qBittorrent, Radarr and Sonarr to protect media: {current['reason']} "
                        f"Path: {current.get('path') or 'not identified'}. "
                        f"Detail: {current.get('detail') or 'unavailable'}. Automatic storage checks will continue.")
            with stack_lock():
                held = list(self.status.get("heldServices", []))
                for name in SERVICES:
                    service = "/run/service/" + name
                    if name not in held and subprocess.check_output(["s6-svstat", "-o", "wantedup", service], text=True).strip() == "true":
                        held.append(name)
                        self.save(heldServices=held)
                        subprocess.run(["s6-svc", "-d", service], check=True, timeout=3)
                for name in held:
                    subprocess.run(["s6-svwait", "-d", "-t", "20000", "/run/service/" + name], check=True, timeout=22)
                self.save(phase="paused", heldServices=held)
        elif self.blocked:
            with stack_lock():
                held = self.status.get("heldServices", [])
                self.notice("Info", "Storage checks passed; restarting held applications: " + (", ".join(held) or "none") + ".")
                for name in held:
                    subprocess.run(["s6-svc", "-u", "/run/service/" + name], check=True, timeout=3)
                self.save(ok=True, phase="restarting", heldServices=[], reason="", path=None)
            self.blocked = False
        else:
            self.save(ok=True, phase="ready", reason="", path=None)

    def poll(self, pool, now):
        if self.future is not None and self.future.done():
            try:
                self.future.result()
            except (OSError, subprocess.SubprocessError, ValueError):
                self.blocked = True
                self.save(ok=False, phase="attention", reason="Unable to confirm that affected applications stopped. Review the storage and application logs.")
                self.notice("Error", self.status["reason"])
            self.future = None
            self.due = now + 15
        if self.future is None and now >= self.due:
            self.future = pool.submit(self.check)
