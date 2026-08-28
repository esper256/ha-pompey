#!/usr/bin/env python3
"""HTTP fakes for grab wiring: Torznab source + qBittorrent WebUI + Seerr.

No BitTorrent protocol. No .torrent files. The "release" is a magnet string
that the download-engine WebAPI would receive. Tests stop when that POST
lands; they never wait on peers or a file on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MOVIE_TITLE = "The Wild Robot"
MOVIE_YEAR = 2024
TMDB_ID = 1184918
IMDB_ID = "29623480"
RELEASE_TITLE = "The Wild Robot 2024 1080p WEB-DL POMPEY"
INFOHASH = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
# xt=urn:btih is the usual magnet form; qBittorrent WebUI takes this as `urls=`.
MAGNET = (
    "magnet:?xt=urn:btih:"
    + INFOHASH
    + "&dn=The.Wild.Robot.2024.1080p.WEB-DL.POMPEY"
    + "&tr=udp://127.0.0.1:9"
)
# Sonarr's indexer test is an empty TV-category search. A movie-only RSS
# feed makes it reject the source ("no results in configured categories")
# and Prowlarr ApplicationIndexerSync then fails.
TV_TITLE = "Pompey Test Show S01E01 1080p WEB-DL"
TV_INFOHASH = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
TV_MAGNET = (
    "magnet:?xt=urn:btih:"
    + TV_INFOHASH
    + "&dn=Pompey.Test.Show.S01E01.1080p.WEB-DL"
    + "&tr=udp://127.0.0.1:9"
)
TV_SIZE_BYTES = 1_073_741_824
API_KEY = "pompey-dev-source"
SIZE_BYTES = 4_294_967_296


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def caps_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="1.1" title="Pompey Fake Source"/>
  <limits max="100" default="50"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,imdbid,tmdbid,tvdbid"/>
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
  </searching>
  <categories>
    <category id="2000" name="Movies">
      <subcat id="2040" name="Movies/HD"/>
      <subcat id="2045" name="Movies/UHD"/>
    </category>
    <category id="5000" name="TV">
      <subcat id="5040" name="TV/HD"/>
    </category>
  </categories>
</caps>
"""


def _cats(params: dict[str, list[str]]) -> set[int]:
    raw = ",".join(params.get("cat") or [])
    out: set[int] = set()
    for part in raw.replace(" ", ",").split(","):
        if part.isdigit():
            out.add(int(part))
    return out


def _t(params: dict[str, list[str]]) -> str:
    return (params.get("t") or ["search"])[0].lower().replace("-", "")


def _query_text(params: dict[str, list[str]]) -> str:
    return " ".join(v[0] for v in (params.get("q"), params.get("query")) if v).lower()


def _include_movie(params: dict[str, list[str]]) -> bool:
    cats = _cats(params)
    if _t(params) == "tvsearch":
        return False
    if cats and all(5000 <= c < 6000 for c in cats):
        return False
    q = _query_text(params)
    tmdb = " ".join((params.get("tmdbid") or params.get("tmdb") or [""]))
    imdb = " ".join((params.get("imdbid") or params.get("imdb") or [""])).replace("tt", "")
    if tmdb and str(TMDB_ID) in tmdb:
        return True
    if imdb and IMDB_ID in imdb:
        return True
    if not q:
        return True
    return any(tok in q for tok in ("wild", "robot"))


def _include_tv(params: dict[str, list[str]]) -> bool:
    cats = _cats(params)
    if _t(params) in {"movie", "moviesearch"}:
        return False
    if cats and all(2000 <= c < 3000 for c in cats):
        return False
    q = _query_text(params)
    if not q:
        return True
    return any(tok in q for tok in ("test show", "s01e01"))


def _item_xml(
    *,
    title: str,
    infohash: str,
    magnet: str,
    size: int,
    category: str,
    extra_attrs: str = "",
) -> str:
    magnet_x = _xml_escape(magnet)
    title_x = _xml_escape(title)
    return f"""    <item>
      <title>{title_x}</title>
      <guid isPermaLink="false">pompey-fake-{infohash}</guid>
      <pubDate>Fri, 28 Aug 2026 00:00:00 +0000</pubDate>
      <size>{size}</size>
      <link>{magnet_x}</link>
      <enclosure url="{magnet_x}" length="{size}" type="application/x-bittorrent"/>
      <torznab:attr name="category" value="{category}"/>
      <torznab:attr name="seeders" value="12"/>
      <torznab:attr name="peers" value="14"/>
      <torznab:attr name="magneturl" value="{magnet_x}"/>
      <torznab:attr name="downloadvolumefactor" value="0"/>
      <torznab:attr name="uploadvolumefactor" value="1"/>
{extra_attrs}    </item>
"""


def search_xml(params: dict[str, list[str]]) -> str:
    items: list[str] = []
    if _include_movie(params):
        items.append(
            _item_xml(
                title=RELEASE_TITLE,
                infohash=INFOHASH,
                magnet=MAGNET,
                size=SIZE_BYTES,
                category="2040",
                extra_attrs=(
                    f'      <torznab:attr name="tmdbid" value="{TMDB_ID}"/>\n'
                    f'      <torznab:attr name="imdb" value="{IMDB_ID}"/>\n'
                ),
            )
        )
    if _include_tv(params):
        items.append(
            _item_xml(
                title=TV_TITLE,
                infohash=TV_INFOHASH,
                magnet=TV_MAGNET,
                size=TV_SIZE_BYTES,
                category="5040",
                extra_attrs=(
                    '      <torznab:attr name="season" value="1"/>\n'
                    '      <torznab:attr name="episode" value="1"/>\n'
                ),
            )
        )
    inner = "".join(items) if items else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Pompey Fake Source</title>
{inner}  </channel>
</rss>
"""


def parse_form(headers: dict, raw: bytes) -> dict[str, str]:
    ctype = headers.get("Content-Type") or headers.get("content-type") or ""
    if "multipart/form-data" in ctype:
        msg = EmailMessage()
        msg["Content-Type"] = ctype
        boundary = msg.get_boundary()
        if not boundary:
            return {}
        parts = raw.split(b"--" + boundary.encode())
        fields: dict[str, str] = {}
        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header, _, body = part.partition(b"\r\n\r\n")
            text = header.decode("latin-1", "replace")
            m = re.search(r'name="([^"]+)"', text)
            if not m:
                continue
            fields[m.group(1)] = body.rsplit(b"\r\n", 1)[0].decode("utf-8", "replace")
        return fields
    if not raw:
        return {}
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


class FakeState:
    def __init__(self, work: Path):
        self.work = work
        self.work.mkdir(parents=True, exist_ok=True)
        self.adds_path = work / "qbit-adds.jsonl"
        self.categories: dict[str, dict] = {}
        self.seerr = {"initialized": False, "radarr": [], "sonarr": []}
        if not self.adds_path.exists():
            self.adds_path.write_text("")

    def record_add(self, payload: dict) -> None:
        with self.adds_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")


def torznab_handler(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[torznab] " + (fmt % args) + "\n")

        def _send(self, code: int, body: str, ctype: str = "application/xml") -> None:
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            path = parsed.path.rstrip("/") or "/"
            if path not in ("/", "/api"):
                return self._send(404, "not found", "text/plain")
            t = (params.get("t") or ["caps"])[0]
            if t == "caps":
                return self._send(200, caps_xml())
            if t in {"search", "movie", "tvsearch", "tv-search", "movie-search"}:
                return self._send(200, search_xml(params))
            return self._send(200, caps_xml())

        do_POST = do_GET

    return Handler


def qbit_handler(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[qbit] " + (fmt % args) + "\n")

        def _send(self, code: int, body, ctype: str = "text/plain", cookie: str | None = None) -> None:
            if isinstance(body, str):
                payload = body.encode()
            elif isinstance(body, (bytes, bytearray)):
                payload = bytes(body)
            else:
                payload = json.dumps(body).encode()
                ctype = "application/json"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.endswith("/version") or path.endswith("/webapiVersion"):
                return self._send(200, "5.0.4")
            if path.endswith("/preferences"):
                return self._send(
                    200,
                    {
                        "current_network_interface": "wg0",
                        "listen_port": 0,
                        "save_path": "/tmp/downloads",
                        "dht": True,
                        "pex": True,
                        "lsd": True,
                    },
                )
            if path.endswith("/categories"):
                return self._send(200, state.categories)
            if path.endswith("/info"):
                qs = parse_qs(urlparse(self.path).query)
                want_cat = (qs.get("category") or [""])[0]
                added = []
                if state.adds_path.exists():
                    for line in state.adds_path.read_text().splitlines():
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if want_cat and (row.get("category") or "") != want_cat:
                            continue
                        added.append(
                            {
                                "hash": INFOHASH,
                                "name": RELEASE_TITLE,
                                "state": "pausedDL",
                                "category": row.get("category") or "",
                                "magnet_uri": row.get("urls") or MAGNET,
                            }
                        )
                return self._send(200, added)
            return self._send(200, [])

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            form = parse_form({k: self.headers[k] for k in self.headers.keys()}, raw)
            if path.endswith("/login"):
                return self._send(200, "Ok.", cookie="SID=pompey-dev; path=/")
            if path.endswith("/createCategory") or path.endswith("/editCategory"):
                name = form.get("category") or form.get("name") or ""
                if name:
                    state.categories[name] = {"name": name, "savePath": form.get("savePath", "")}
                return self._send(200, "Ok.")
            if path.endswith("/add"):
                payload = {
                    "urls": form.get("urls") or form.get("urls[]") or "",
                    "category": form.get("category") or "",
                    "savepath": form.get("savepath") or form.get("savePath") or "",
                    "paused": form.get("paused") or "",
                }
                state.record_add(payload)
                return self._send(200, "Ok.")
            return self._send(200, "Ok.")

    return Handler


def seerr_handler(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("[seerr] " + (fmt % args) + "\n")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            try:
                return json.loads(raw.decode())
            except json.JSONDecodeError:
                return {}

        def _send(self, code: int, body, ctype: str = "application/json", cookie: str | None = None) -> None:
            if isinstance(body, str):
                payload = body.encode()
            elif isinstance(body, (bytes, bytearray)):
                payload = bytes(body)
            else:
                payload = json.dumps(body).encode()
                ctype = "application/json"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/login", "/setup"):
                html = b'<!doctype html><script src="/_next/static/x.js"></script>'
                return self._send(200, html, "text/html")
            if path.endswith("/settings/radarr"):
                return self._send(200, state.seerr["radarr"])
            if path.endswith("/settings/sonarr"):
                return self._send(200, state.seerr["sonarr"])
            if path.endswith("/settings/public"):
                return self._send(200, {"initialized": state.seerr["initialized"]})
            if path.endswith("/settings/main"):
                return self._send(200, {"apiKey": "stub-seerr-key"})
            if path.endswith("/settings/plex/devices/servers"):
                return self._send(200, [])
            if path.endswith("/settings/plex/library"):
                return self._send(200, [])
            return self._send(200, {"ok": True})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = self._body()
            if path.endswith("/settings/radarr"):
                state.seerr["radarr"].append(body)
                return self._send(201, body)
            if path.endswith("/settings/sonarr"):
                state.seerr["sonarr"].append(body)
                return self._send(201, body)
            if path.endswith("/auth/local") or path.endswith("/auth/plex"):
                return self._send(200, {"id": 1}, cookie="connect.sid=int; Path=/; HttpOnly")
            if path.endswith("/initialize"):
                state.seerr["initialized"] = True
                return self._send(200, {"initialized": True})
            return self._send(200, body or {"ok": True})

        do_PUT = do_POST

    return Handler


def serve(work: Path, host: str = "127.0.0.1", qbit_port: int = 8080, seerr_port: int = 5055, torznab_port: int = 9117) -> None:
    state = FakeState(work)
    servers = [
        ThreadingHTTPServer((host, qbit_port), qbit_handler(state)),
        ThreadingHTTPServer((host, seerr_port), seerr_handler(state)),
        ThreadingHTTPServer((host, torznab_port), torznab_handler(state)),
    ]
    for srv in servers:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    (work / "http-stub.pid").write_text(str(os.getpid()))
    (work / "fake-source.json").write_text(
        json.dumps(
            {
                "qbit": f"http://{host}:{qbit_port}",
                "seerr": f"http://{host}:{seerr_port}",
                "torznab": f"http://{host}:{torznab_port}",
                "api_key": API_KEY,
                "magnet": MAGNET,
                "infohash": INFOHASH,
                "title": RELEASE_TITLE,
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"fake source on {host}: qbit={qbit_port} seerr={seerr_port} torznab={torznab_port}",
        flush=True,
    )
    threading.Event().wait()


def http_json(method: str, url: str, body=None, headers=None, timeout: int = 30):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return None
            text = raw.decode(errors="replace")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    except urllib.error.HTTPError as exc:
        err = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> {exc.code} {err[:400]}") from exc


def _as_list(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def prowlarr_ensure_qbit(prowlarr: str, api_key: str, user: str, password: str) -> None:
    hdrs = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    existing = _as_list(http_json("GET", f"{prowlarr}/api/v1/downloadclient", headers=hdrs))
    if any(item.get("implementation") == "QBittorrent" for item in existing):
        return
    schema = http_json("GET", f"{prowlarr}/api/v1/downloadclient/schema", headers=hdrs)
    client = None
    for item in _as_list(schema):
        if item.get("implementation") == "QBittorrent":
            client = item
            break
    if client is None:
        raise RuntimeError("Prowlarr schema missing QBittorrent")
    for field in client.get("fields") or []:
        name = field.get("name")
        if name == "host":
            field["value"] = "127.0.0.1"
        elif name == "port":
            field["value"] = 8080
        elif name in {"username", "userName"}:
            field["value"] = user
        elif name == "password":
            field["value"] = password
        elif name in {"category", "movieCategory", "tvCategory"}:
            field["value"] = "radarr"
        elif name == "useSsl":
            field["value"] = False
    client["name"] = "qBittorrent"
    client["enable"] = True
    client["priority"] = 1
    http_json("POST", f"{prowlarr}/api/v1/downloadclient", client, headers=hdrs)


def prowlarr_search(prowlarr: str, api_key: str, query: str, tries: int = 12) -> list[dict]:
    hdrs = {"X-Api-Key": api_key}
    q = urllib.parse.quote(query)
    last: list[dict] = []
    for _ in range(tries):
        data = http_json("GET", f"{prowlarr}/api/v1/search?query={q}&type=search&limit=50", headers=hdrs)
        last = _as_list(data)
        blob = json.dumps(last).lower()
        if any(tok in blob for tok in ("wild robot", INFOHASH.lower(), "pompey")):
            return last
        time.sleep(2)
    return last


def prowlarr_grab(prowlarr: str, api_key: str, release: dict) -> object:
    hdrs = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    return http_json("POST", f"{prowlarr}/api/v1/search", release, headers=hdrs, timeout=60)


def wait_qbit_add(adds_path: Path, needle: str = INFOHASH, tries: int = 20) -> dict:
    for _ in range(tries):
        if adds_path.is_file():
            for line in adds_path.read_text().splitlines():
                if needle.lower() in line.lower():
                    return json.loads(line)
        time.sleep(1)
    raise RuntimeError(f"qBittorrent WebUI never got an add containing {needle}")


def grab_cli(prowlarr: str, api_key: str, query: str, adds: Path, user: str, password: str) -> dict:
    prowlarr_ensure_qbit(prowlarr, api_key, user, password)
    hits = prowlarr_search(prowlarr, api_key, query)
    if not hits:
        raise RuntimeError(f"Prowlarr search returned no hits for {query!r}")
    pick = hits[0]
    title = pick.get("title") or ""
    blob = json.dumps(pick)
    if INFOHASH.lower() not in blob.lower() and "wild robot" not in title.lower() and "pompey" not in title.lower():
        raise RuntimeError(f"unexpected first hit: {title!r}")
    prowlarr_grab(prowlarr, api_key, pick)
    return wait_qbit_add(adds)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    serve_p = sub.add_parser("serve")
    serve_p.add_argument("--work", required=True)
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--qbit-port", type=int, default=8080)
    serve_p.add_argument("--seerr-port", type=int, default=5055)
    serve_p.add_argument("--torznab-port", type=int, default=9117)
    grab_p = sub.add_parser("grab")
    grab_p.add_argument("--prowlarr", required=True)
    grab_p.add_argument("--key", required=True)
    grab_p.add_argument("--query", default=MOVIE_TITLE)
    grab_p.add_argument("--adds", required=True)
    grab_p.add_argument("--user", default="pompey")
    grab_p.add_argument("--password", required=True)
    args = p.parse_args(argv)
    if args.cmd == "grab":
        added = grab_cli(
            args.prowlarr.rstrip("/"),
            args.key,
            args.query,
            Path(args.adds),
            args.user,
            args.password,
        )
        print(json.dumps(added, indent=2))
        return 0
    serve(Path(args.work), args.host, args.qbit_port, args.seerr_port, args.torznab_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
