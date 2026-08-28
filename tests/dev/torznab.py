#!/usr/bin/env python3
"""Fake Torznab + a tiny legal fixture torrent.

Radarr matches on the release *name* (The Wild Robot). The payload is a few
kilobytes of text we generated, not a movie. qBittorrent is told to fetch
the webseed via the fake wg0 address (10.2.0.1) so bound sockets hit wg0.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TITLE = "The Wild Robot 2024 1080p WEB-DL"
TMDB = "1184918"
YEAR = "2024"
PIECE_LEN = 16384
PAYLOAD = (b"Pompey integration fixture. Not a movie.\n" * 400)[:PIECE_LEN]


def benc(value) -> bytes:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"i{value}e".encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return benc(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(benc(v) for v in value) + b"e"
    if isinstance(value, dict):
        out = b"d"
        for key in sorted(value, key=lambda k: k if isinstance(k, bytes) else str(k).encode()):
            kb = key if isinstance(key, bytes) else str(key).encode()
            out += benc(kb) + benc(value[key])
        return out + b"e"
    raise TypeError(type(value))


def make_torrent(webseed: str, announce: str) -> bytes:
    piece = hashlib.sha1(PAYLOAD).digest()
    info = {
        b"name": b"The Wild Robot (2024) 1080p WEB-DL.mkv",
        b"length": len(PAYLOAD),
        b"piece length": PIECE_LEN,
        b"pieces": piece,
    }
    return benc(
        {
            b"announce": announce.encode(),
            b"url-list": webseed.encode(),
            b"info": info,
        }
    )


def torznab_caps() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="1.0" title="Pompey fixture"/>
  <limits default="100" max="100"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid,year"/>
  </searching>
  <categories>
    <category id="2000" name="Movies">
      <subcat id="2040" name="Movies/HD"/>
    </category>
  </categories>
</caps>
"""


def torznab_rss(items_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Pompey fixture</title>
    {items_xml}
  </channel>
</rss>
"""


def movie_item(download: str) -> str:
    size = str(len(PAYLOAD))
    return f"""<item>
  <title>{TITLE}</title>
  <guid isPermaLink="false">pompey-dev-{TMDB}</guid>
  <pubDate>Tue, 01 Oct 2024 00:00:00 +0000</pubDate>
  <size>{size}</size>
  <link>{download}</link>
  <enclosure url="{download}" length="{size}" type="application/x-bittorrent"/>
  <torznab:attr name="category" value="2040"/>
  <torznab:attr name="seeders" value="50"/>
  <torznab:attr name="peers" value="50"/>
  <torznab:attr name="size" value="{size}"/>
  <torznab:attr name="tmdbid" value="{TMDB}"/>
  <torznab:attr name="year" value="{YEAR}"/>
</item>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PompeyTorznab/0.1"

    def log_message(self, *_args, **_kwargs):
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        cfg = self.server.cfg  # type: ignore[attr-defined]
        if parsed.path in {"/download/wild-robot.torrent", "/wild-robot.torrent"}:
            self._send(200, cfg["torrent"], "application/x-bittorrent")
            return
        if parsed.path in {"/payload.bin", "/file"}:
            self._send(200, PAYLOAD, "application/octet-stream")
            return
        if parsed.path == "/announce":
            # Compact tracker with no peers; webseed does the work.
            self._send(200, benc({b"interval": 1800, b"peers": b""}), "text/plain")
            return
        if parsed.path in {"/api", "/"}:
            kind = (qs.get("t") or ["search"])[0]
            if kind == "caps":
                self._send(200, torznab_caps().encode(), "application/xml")
                return
            q = " ".join(qs.get("q") or []).lower()
            tmdb = " ".join(qs.get("tmdbid") or qs.get("tmdb") or [])
            want = (not q and not tmdb) or "wild" in q or "robot" in q or tmdb == TMDB
            items = movie_item(cfg["download"]) if want else ""
            self._send(200, torznab_rss(items).encode(), "application/xml")
            return
        self._send(404, b"nope", "text/plain")


def serve(host: str, port: int, public_base: str) -> ThreadingHTTPServer:
    torrent = make_torrent(f"{public_base}/payload.bin", f"{public_base}/announce")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.cfg = {
        "torrent": torrent,
        "download": f"{public_base}/download/wild-robot.torrent",
    }
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main() -> int:
    host = os.environ.get("TORZNAB_HOST", "0.0.0.0")
    port = int(os.environ.get("TORZNAB_PORT", "9117"))
    public = os.environ.get("TORZNAB_PUBLIC", f"http://10.2.0.1:{port}").rstrip("/")
    httpd = serve(host, port, public)
    print(f"torznab listening on {host}:{port} public {public}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
