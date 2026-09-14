#!/usr/bin/env python3
"""Accept a VPN WireGuard .conf from the wait screen. Do not log secrets."""
from __future__ import annotations

import json
import os
import tempfile
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 64 * 1024
LISTEN = os.environ.get("POMPEY_SETUP_LISTEN", "127.0.0.1:8097")


def validate_wg(text: str) -> str:
    """Return an error string, or empty if this looks like a VPN WireGuard file."""
    from vpn_config import parse
    try:
        parse(text)
        return ""
    except (ValueError, OSError) as exc:
        return str(exc)


def wg_file() -> str:
    config = os.environ.get("POMPEY_CONFIG", "/config")
    return os.path.join(config, "wireguard", "wg0.conf")


def save_wg(text: str) -> None:
    path = wg_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".wg-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cleaned)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _log(msg: str, level: str = "INFO") -> None:
    stamp = time.strftime("%H:%M:%S")
    sys.stderr.write(f"[{stamp}] {level}: pompey_setup.py: {msg}\n")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        _log(fmt % args)

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path not in {"/setup/vpn", "/vpn"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._send(400, {"ok": False, "error": "That file is too large. Paste only the VPN .conf."})
            return
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        text = raw.decode("utf-8", errors="replace")
        if ctype == "application/json":
            try:
                payload = json.loads(text or "{}")
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "Could not read that paste."})
                return
            if not isinstance(payload, dict):
                self._send(400, {"ok": False, "error": "Expected a configuration object."})
                return
            text = str(payload.get("config") or payload.get("text") or "")
        err = validate_wg(text)
        if err:
            self._send(400, {"ok": False, "error": err})
            return
        save_wg(text)
        # The WireGuard service is the only runtime configuration writer.
        # It detects the saved file and applies it, including after rotation.
        self._send(200, {"ok": True})


def serve() -> int:
    host, _, port_s = LISTEN.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port_s or "8097")), Handler)
    server.serve_forever()
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] and argv[1] == "--validate":
        path = argv[2] if len(argv) > 2 else "-"
        text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        err = validate_wg(text)
        if err:
            print(err, file=sys.stderr)
            return 3
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
