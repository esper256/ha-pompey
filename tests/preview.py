#!/usr/bin/env python3
"""Serve the Pompey wait screen with live status. No Home Assistant required.

  python3 tests/preview.py

Then open http://127.0.0.1:8099/ — the bar moves through first-boot steps, then
hides. This is the Pompey sidebar (wait, then dashboard). Search is Seerr on
host port 5055 and sources are Prowlarr on 9696, not this page (`status.json`
has `"search": true` plus `"search_port"` and `"sources_port"`). Opening the
page only reads status; it does not re-run setup.

Pass ``--debug`` to set ``POMPEY_DEBUG=1`` (same as the Home Assistant Debug
option). The dashboard then offers Radarr / Sonarr / qBittorrent links under
``/debug/…``, served here as stand-in pages so you can click them without
engines.
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
        path = self.path.split("?", 1)[0]
        if path == "/status.json":
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
        if path == "/debug/shim.js":
            shim = STATIC / "debug-shim.js"
            body = shim.read_bytes() if shim.is_file() else b""
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        fake = preview_debug_page(path)
        if fake is not None:
            body, ctype = fake
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        return super().do_GET()


DEBUG_TITLES = {
    "radarr": "Radarr",
    "sonarr": "Sonarr",
    "qbittorrent": "qBittorrent",
}


def preview_debug_page(path: str):
    """Stand-in engine pages for --debug. Production nginx proxies the real UIs."""
    if path in {"/debug/radarr", "/debug/sonarr", "/debug/qbittorrent"}:
        path = path + "/"
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "debug":
        return None
    name = parts[1]
    title = DEBUG_TITLES.get(name)
    if not title:
        return None
    if len(parts) >= 3 and parts[2] == "api":
        return ('{"instanceName":"%s","preview":true}\n' % title, "application/json")
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:ui-sans-serif,system-ui,sans-serif;background:#07090d;color:#ece8e1;padding:2rem">
  <p style="color:#9a948a;text-transform:uppercase;letter-spacing:.04em;font-size:.75rem">Pompey debug preview</p>
  <h1>{title}</h1>
  <p>This stand-in is only <code>tests/preview.py --debug</code>. On Home Assistant, Ingress proxies the real Web UI.</p>
  <p id="api">Checking /api…</p>
  <script src="/debug/shim.js"></script>
  <script>
    fetch("/api/v3/system/status").then((r) => r.json()).then((data) => {{
      document.getElementById("api").textContent = "API via shim: " + (data.instanceName || "ok");
    }}).catch((exc) => {{
      document.getElementById("api").textContent = "API via shim failed: " + exc;
    }});
  </script>
</body></html>
"""
    return (html, "text/html; charset=utf-8")


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
    Path(env["POMPEY_READY"]).joinpath("wired").touch()
    if hold_ready:
        # Same chatter engines/wire emit on restart. Must not rewind the bar.
        while True:
            status(env, "fetch", "Downloading hidden engines", "30")
            time.sleep(30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8099")))
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds between demo steps")
    parser.add_argument("--no-demo", action="store_true", help="Do not animate steps; only serve")
    parser.add_argument("--once", action="store_true", help="Stop after reaching ready (for tests)")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Same as the Home Assistant Debug option: show engine console links",
    )
    args = parser.parse_args()

    work = Path(os.environ.get("POMPEY_READY", "/tmp/pompey-preview"))
    work.mkdir(parents=True, exist_ok=True)
    status_path = work / "status.json"
    if status_path.exists():
        status_path.unlink()
    env = os.environ.copy()
    env["POMPEY_READY"] = str(work)
    env["POMPEY_STATUS"] = str(status_path)
    if args.debug:
        env["POMPEY_DEBUG"] = "1"
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
