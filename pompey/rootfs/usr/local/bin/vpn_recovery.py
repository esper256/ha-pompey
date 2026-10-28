"""Reopen downloader sockets after replacement of their bound VPN interface."""

import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from pompey_runtime import atomic_json, stack_lock


def main():
    ready = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
    path = ready / "vpn-recovery.json"
    generation = int(Path("/sys/class/net/wg0/ifindex").read_text())
    previous = json.loads(path.read_text()) if path.exists() else {}
    if previous.get("interface") == generation:
        return
    if not previous:
        atomic_json(path, {"interface": generation, "phase": "initial", "updated": time.time()})
        return
    service = "/run/service/qbittorrent"
    # Do not change a service that another administrator/policy deliberately
    # stopped. Restarting the process preserves native per-torrent stop state.
    record = {"interface": generation, "phase": "reconnecting", "updated": time.time()}
    try:
        with stack_lock():
            wanted = subprocess.check_output(["s6-svstat", "-o", "wantedup", service], text=True).strip()
            if wanted != "true":
                return
            atomic_json(path, record)
            stopped = False
            try:
                subprocess.run(["s6-svc", "-d", service], check=True, timeout=3)
                stopped = True
                subprocess.run(["s6-svwait", "-d", "-t", "20000", service], check=True, timeout=22)
            finally:
                if stopped:
                    subprocess.run(["s6-svc", "-u", service], check=True, timeout=3)
        # The startup import gate needs stack_lock. Observe readiness only after
        # releasing it; holding it here prevents the restarted service starting.
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8080/api/v2/app/version", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Downloader did not restart after VPN interface replacement")
            time.sleep(.2)
        record.update(phase="reconnected", updated=time.time())
        print("Downloader connections reopened after VPN interface replacement", flush=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        record.update(phase="failed", error=str(exc), updated=time.time())
        print("Download recovery needs attention; inspect application status", flush=True)
    atomic_json(path, record)


if __name__ == "__main__":
    main()
