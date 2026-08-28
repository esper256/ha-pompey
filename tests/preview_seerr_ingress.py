#!/usr/bin/env python3
"""Serve real Seerr the way Home Assistant Ingress does.

Supervisor strips /api/hassio_ingress/<token> and sets X-Ingress-Path.
This wrapper does the same, then pompey-ingress rewrites root-absolute URLs.

  python3 tests/preview_seerr_ingress.py

Then open http://127.0.0.1:18099/api/hassio_ingress/tok/setup
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/lib"))
import seerr_runtime as seerr  # noqa: E402

PREFIX_DEFAULT = "/api/hassio_ingress/tok"
BIN = ROOT / "pompey/rootfs/usr/local/bin/pompey-ingress"


def load_ingress():
    loader = importlib.machinery.SourceFileLoader("pompey_ingress", str(BIN))
    spec = importlib.util.spec_from_loader("pompey_ingress", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def wrap(ing, prefix: str):
    class Handler(ing.Handler):
        def parse_request(self):
            ok = super().parse_request()
            if not ok:
                return False
            path = self.path
            q = ""
            if "?" in path:
                path, q = path.split("?", 1)
                q = "?" + q
            if path.startswith(prefix):
                rest = path[len(prefix) :] or "/"
                self.path = rest + q
            return True

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "18099")))
    parser.add_argument("--prefix", default=os.environ.get("INGRESS_PATH", PREFIX_DEFAULT))
    args = parser.parse_args()
    prefix = args.prefix.rstrip("/") or PREFIX_DEFAULT

    proc = seerr.SeerrProcess().start()
    os.environ["SEERR_URL"] = proc.url
    os.environ["INGRESS_PATH"] = prefix
    os.environ["POMPEY_INGRESS_PROXY"] = f"http://127.0.0.1:{args.port}"

    ing = load_ingress()
    handler = wrap(ing, prefix)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}{prefix}/setup"
    print(f"[{time.strftime('%H:%M:%S')}] INFO: Seerr {proc.url}", flush=True)
    print(f"[{time.strftime('%H:%M:%S')}] INFO: Ingress replica {url}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        proc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
