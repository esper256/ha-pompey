#!/usr/bin/env python3
"""Serve the Pompey wait screen with live status. No Home Assistant required.

  python3 tests/preview.py

Then open http://127.0.0.1:8099/ — the bar moves through the same steps the
addon reports. This is the Pompey sidebar (wait/status). Search is Seerr on
host port 5055 and sources are Prowlarr on 9696, not this page (`status.json`
has `"search": true` plus `"search_port"` and `"sources_port"`).
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "pompey/rootfs/usr/share/pompey"
STATUS_BIN = ROOT / "pompey/rootfs/usr/local/bin/pompey-status"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, status_path: Path, **kwargs):
        self._status_path = status_path
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        if self.path.split("?", 1)[0] == "/status.json":
            return
        stamp = time.strftime("%H:%M:%S")
        sys.stderr.write("[%s] INFO: %s - %s\n" % (stamp, self.address_string(), fmt % args))

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/status.json":
            body = b'{"step":"vpn","label":"Starting","percent":5,"error":"","steps":[]}\n'
            if self._status_path.is_file():
                body = self._status_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


def status(env: dict, *args: str) -> None:
    subprocess.run([sys.executable, str(STATUS_BIN), *args], check=True, env=env)


def write_fake_netdev(path: Path, rx: int, tx: int) -> None:
    path.write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|"
        "bytes    packets errs drop fifo frame compressed\n"
        f"  wg0: {rx} 12 0 0 0 0 0 0 {tx} 8 0 0 0 0 0 0\n"
    )


def vpn_demo(env: dict, netdev: Path, stop: threading.Event) -> None:
    stats = ROOT / "pompey/rootfs/usr/local/bin/pompey-vpn-stats"
    rx, tx = 18_000_000, 1_200_000
    child = env.copy()
    child["POMPEY_NET_DEV"] = str(netdev)
    while not stop.is_set():
        rx += random.randint(40_000, 2_200_000)
        tx += random.randint(4_000, 220_000)
        write_fake_netdev(netdev, rx, tx)
        subprocess.run([sys.executable, str(stats)], check=False, env=child)
        stop.wait(1)


def demo(env: dict, hold_ready: bool, delay: float) -> None:
    sequence = [
        ("vpn", "Starting", "5"),
        ("vpn", "Bringing up the Proton tunnel", "10"),
        ("vpn", "Waiting for Proton handshake", "15"),
        ("fetch", "Downloading hidden engines", "35"),
        ("fetch", "Downloading the household UI", "55"),
        ("start", "Starting hidden engines", "70"),
        ("wire", "Connecting search to your library", "85"),
        ("ready", "Ready", "100"),
    ]
    for step in sequence:
        status(env, *step)
        time.sleep(delay)
    if hold_ready:
        while True:
            time.sleep(30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8099")))
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds between demo steps")
    parser.add_argument("--no-demo", action="store_true", help="Do not animate steps; only serve")
    parser.add_argument("--once", action="store_true", help="Stop after reaching ready (for tests)")
    args = parser.parse_args()

    work = Path(os.environ.get("POMPEY_READY", "/tmp/pompey-preview"))
    work.mkdir(parents=True, exist_ok=True)
    status_path = work / "status.json"
    if status_path.exists():
        status_path.unlink()
    env = os.environ.copy()
    env["POMPEY_READY"] = str(work)
    env["POMPEY_STATUS"] = str(status_path)
    status(env, "vpn", "Starting", "5")
    netdev = work / "net-dev"
    write_fake_netdev(netdev, 18_000_000, 1_200_000)
    env["POMPEY_NET_DEV"] = str(netdev)
    vpn_stop = threading.Event()
    threading.Thread(
        target=vpn_demo, args=(env, netdev, vpn_stop), daemon=True
    ).start()

    handler = lambda *a, **k: Handler(*a, status_path=status_path, **k)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Pompey wait screen: http://127.0.0.1:{args.port}/", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        if not args.no_demo:
            demo(env, hold_ready=not args.once, delay=args.delay)
            if args.once:
                time.sleep(0.5)
                return 0
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        return 0
    finally:
        vpn_stop.set()
        httpd.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
