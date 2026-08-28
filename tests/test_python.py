#!/usr/bin/env python3
"""HAOS is not required: supply Supervisor options.json and fake engine HTTP."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import struct
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

os.environ.setdefault("POMPEY_WAIT_TRIES", "8")
os.environ.setdefault("POMPEY_WAIT_SLEEP", "0.01")

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "pompey/rootfs/usr/local/bin"
OPTIONS = json.loads((ROOT / "tests/options.json").read_text())


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ws = load("wire_stack", BIN / "wire-stack")
rr = load("route_rating", BIN / "route-rating")
wqc = load("wg_quick_contract", ROOT / "tests/lib/wg_quick_contract.py")


def png_wh(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def yaml_indent2_keys(text: str, header: str) -> list[str]:
    keys: list[str] = []
    in_sec = False
    for line in text.splitlines():
        if line.rstrip() == header:
            in_sec = True
            continue
        if not in_sec:
            continue
        if line and not line.startswith(" ") and not line.startswith("\t") and not line.startswith("#"):
            break
        if line.startswith("  ") and not line.startswith("    "):
            key = line.strip().split(":", 1)[0]
            if key:
                keys.append(key)
    return keys


class FakeState:
    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []
        self.radarr_folders: list[str] = []
        self.sonarr_folders: list[str] = []
        self.download_clients: list[dict] = []
        self.apps: list[dict] = []
        self.indexers: list[dict] = []
        self.commands: list[dict] = []
        self.arr_commands: list[dict] = []
        self.history: list[dict] = []
        self.plex_auth: object = None
        self.local_auth: object = None
        self.allow_seerr_local = False
        self.seerr_object_lists = False
        self.seerr_has_admin = False
        self.seerr_radarr: list[dict] = []
        self.seerr_sonarr: list[dict] = []
        self.initialized = False
        self.fail_seerr_radarr = False
        self.fail_indexer = False
        self.qbit_prefs: object = None
        self.movies = [
            {"id": 1, "title": "Kid Flick", "certification": "PG", "path": "/media/Movies/Not Kid Friendly/Kid Flick"},
            {"id": 2, "title": "Unknown", "certification": "", "path": "/media/Movies/Not Kid Friendly/Unknown"},
            {"id": 3, "title": "Already Kid", "certification": "G", "path": "/media/Movies/Kid Friendly/Already Kid"},
            {
                "id": 4,
                "title": "Nested Kid",
                "ratings": {"tmdb": {"certification": "PG"}},
                "path": "/media/Movies/Not Kid Friendly/Nested Kid",
            },
        ]
        self.series = [
            {"id": 10, "title": "Adult Show", "certification": "TV-MA", "path": "/media/TV/Not Kid Friendly/Adult Show"},
        ]
        self.moved: list[dict] = []


def handler_for(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return

        def _read(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return None
            try:
                return json.loads(raw.decode())
            except json.JSONDecodeError:
                return parse_qs(raw.decode())

        def _send(self, code=200, body=None, text=None, cookie=None):
            payload = b""
            if text is not None:
                payload = text.encode()
                ctype = "text/plain"
            elif body is not None:
                payload = json.dumps(body).encode()
                ctype = "application/json"
            else:
                ctype = "application/json"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def _handle(self, method: str):
            role = getattr(self.server, "role")
            path = self.path.split("?", 1)[0]
            body = self._read() if method in {"POST", "PUT"} else None
            state.calls.append((role, method, path, body))
            if role == "qbit":
                if path == "/api/v2/app/version":
                    return self._send(text="5.0.4")
                if path == "/api/v2/torrents/createCategory":
                    return self._send(text="Ok.")
                if path == "/api/v2/auth/login":
                    return self._send(text="Ok.")
                if path == "/api/v2/app/setPreferences":
                    state.qbit_prefs = body
                    return self._send(text="Ok.")
                return self._send(404, {"error": path})
            if role in {"sonarr", "radarr"}:
                folders = state.sonarr_folders if role == "sonarr" else state.radarr_folders
                if path == "/ping":
                    return self._send(body={"status": "OK"})
                if path.endswith("/rootfolder") and method == "GET":
                    return self._send(body=[{"path": p} for p in folders])
                if path.endswith("/rootfolder") and method == "POST":
                    folders.append((body or {}).get("path"))
                    return self._send(201, body)
                if path.endswith("/downloadclient") and method == "GET":
                    return self._send(body=[])
                if path.endswith("/downloadclient/schema"):
                    return self._send(
                        body=[
                            {
                                "implementation": "QBittorrent",
                                "fields": [
                                    {"name": n} for n in
                                    ("host", "port", "username", "password", "movieCategory",
                                     "tvCategory", "category", "useSsl")
                                ],
                            }
                        ]
                    )
                if path.endswith("/downloadclient") and method == "POST":
                    state.download_clients.append(body)
                    return self._send(201, body)
                if path.endswith("/qualityprofile"):
                    return self._send(body=[{"id": 1, "name": "HD-1080p"}])
                if path.endswith("/languageprofile"):
                    return self._send(body=[])
                if path.endswith("/movie") and method == "GET":
                    return self._send(body=state.movies)
                if "/movie/" in path and method == "PUT":
                    state.moved.append(body)
                    return self._send(body=body)
                if path.endswith("/series") and method == "GET":
                    return self._send(body=state.series)
                if "/series/" in path and method == "PUT":
                    state.moved.append(body)
                    return self._send(body=body)
                if path.endswith("/command") and method == "POST":
                    state.arr_commands.append(body or {})
                    return self._send(201, body or {})
                return self._send(404, {"error": path})
            if role == "prowlarr":
                if path == "/ping":
                    return self._send(body={"status": "OK"})
                if path == "/api/v1/applications" and method == "GET":
                    return self._send(body=state.apps)
                if path == "/api/v1/applications/schema":
                    fields = [{"name": n} for n in ("prowlarrUrl", "baseUrl", "apiKey")]
                    return self._send(
                        body=[
                            {"implementation": "Sonarr", "fields": fields},
                            {"implementation": "Radarr", "fields": fields},
                        ]
                    )
                if path == "/api/v1/applications" and method == "POST":
                    state.apps.append(body)
                    return self._send(201, body)
                if path == "/api/v1/indexer" and method == "GET":
                    return self._send(body=state.indexers)
                if path == "/api/v1/indexer/schema":
                    fields = [{"name": n} for n in ("baseUrl", "apiPath", "apiKey")]
                    return self._send(body=[{"implementation": "Torznab", "fields": fields}])
                if path == "/api/v1/indexer" and method == "POST":
                    if state.fail_indexer:
                        return self._send(500, {"message": "indexer add failed"})
                    state.indexers.append(body)
                    return self._send(201, body)
                if path.startswith("/api/v1/indexer/") and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(state.indexers):
                        if item.get("id") == idx:
                            state.indexers[i] = body or item
                            return self._send(body=state.indexers[i])
                    return self._send(404, {"error": path})
                if path == "/api/v1/command" and method == "POST":
                    state.commands.append(body or {})
                    return self._send(201, body or {"name": "ApplicationIndexerSync"})
                if path == "/api/v1/history" and method == "GET":
                    return self._send(
                        body={
                            "page": 1,
                            "pageSize": 40,
                            "totalRecords": len(state.history),
                            "records": state.history,
                        }
                    )
                return self._send(404, {"error": path})
            if role == "seerr":
                if path == "/api/v1/settings/public":
                    return self._send(body={"initialized": state.initialized})
                if path == "/api/v1/auth/plex":
                    state.plex_auth = body
                    return self._send(body={"id": 1}, cookie="connect.sid=testcookie; Path=/")
                if path == "/api/v1/auth/local":
                    # Real Seerr is login-only (see tests/test_seerr_real.py). Default 403.
                    if not state.allow_seerr_local:
                        return self._send(403, {"message": "Access denied."})
                    state.local_auth = body
                    return self._send(body={"id": 1, "email": (body or {}).get("email")}, cookie="connect.sid=local; Path=/")
                # Real Seerr: X-API-Key impersonates user id 1. That row does not
                # exist until Plex login or the setup wizard creates the first admin.
                key = self.headers.get("X-API-Key") or self.headers.get("X-Api-Key") or ""
                cookie = (self.headers.get("Cookie") or "").strip()
                has_admin = bool(state.plex_auth or state.local_auth or state.seerr_has_admin)
                valid_key = key in {"seerr-disk-key", "seerr-api-key"}
                if not cookie and not (valid_key and has_admin):
                    return self._send(
                        403,
                        {"status": 403, "error": "You do not have permission to access this endpoint"},
                    )
                if path == "/api/v1/settings/main" and method == "GET":
                    return self._send(body={"apiKey": "seerr-api-key"})
                if path == "/api/v1/settings/main" and method == "POST":
                    return self._send(body=body)
                if path == "/api/v1/settings/plex/devices/servers":
                    return self._send(
                        body=[
                            {
                                "name": "Living Room",
                                "owned": True,
                                "provides": ["server"],
                                "clientIdentifier": "machine-1",
                                "connection": [{"address": "172.30.32.1", "port": 32400}],
                            }
                        ]
                    )
                if path == "/api/v1/settings/plex" and method == "POST":
                    return self._send(body=body)
                if path == "/api/v1/settings/plex/library/sync":
                    return self._send(body=[{"id": "1", "name": "Movies", "enabled": False}])
                if path == "/api/v1/settings/plex/library":
                    return self._send(body=[{"id": "1", "name": "Movies", "enabled": False}])
                if path.startswith("/api/v1/settings/plex/library/") and method == "PUT":
                    return self._send(body={"id": "1", "enabled": True})
                if path == "/api/v1/settings/radarr" and method == "GET":
                    if state.seerr_object_lists:
                        return self._send(body={"initialized": False})
                    return self._send(body=state.seerr_radarr)
                if path == "/api/v1/settings/radarr" and method == "POST":
                    if state.fail_seerr_radarr:
                        return self._send(500, {"message": "radarr wiring failed"})
                    state.seerr_radarr.append(body)
                    return self._send(201, body)
                if path == "/api/v1/settings/sonarr" and method == "GET":
                    if state.seerr_object_lists:
                        return self._send(body={"initialized": False})
                    return self._send(body=state.seerr_sonarr)
                if path == "/api/v1/settings/sonarr" and method == "POST":
                    state.seerr_sonarr.append(body)
                    return self._send(201, body)
                if path.startswith("/api/v1/settings/radarr/") and method == "PUT":
                    if isinstance(body, dict) and "id" in body:
                        return self._send(
                            400,
                            {
                                "message": "request/body/id is read-only",
                                "errors": [
                                    {
                                        "path": "/body/id",
                                        "message": "is read-only",
                                        "errorCode": "readOnly.openapi.validation",
                                    }
                                ],
                            },
                        )
                    updated = dict(body or {})
                    if state.seerr_radarr:
                        state.seerr_radarr[0] = {**state.seerr_radarr[0], **updated}
                    else:
                        state.seerr_radarr.append(updated)
                    return self._send(body=state.seerr_radarr[0] if state.seerr_radarr else updated)
                if path.startswith("/api/v1/settings/sonarr/") and method == "PUT":
                    if isinstance(body, dict) and "id" in body:
                        return self._send(
                            400,
                            {
                                "message": "request/body/id is read-only",
                                "errors": [
                                    {
                                        "path": "/body/id",
                                        "message": "is read-only",
                                        "errorCode": "readOnly.openapi.validation",
                                    }
                                ],
                            },
                        )
                    updated = dict(body or {})
                    if state.seerr_sonarr:
                        state.seerr_sonarr[0] = {**state.seerr_sonarr[0], **updated}
                    else:
                        state.seerr_sonarr.append(updated)
                    return self._send(body=state.seerr_sonarr[0] if state.seerr_sonarr else updated)
                if path == "/api/v1/settings/network":
                    return self._send(body=body)
                if path == "/api/v1/settings/initialize":
                    state.initialized = True
                    return self._send(body={"initialized": True})
                return self._send(404, {"error": path})
            return self._send(500, {"error": "no role"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

    return Handler


def start_role(role: str, state: FakeState):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(state))
    httpd.role = role
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


class OptionsMatchConfig(unittest.TestCase):
    def test_options_json_covers_config_yaml(self):
        cfg = (ROOT / "pompey/config.yaml").read_text()
        trans = (ROOT / "pompey/translations/en.yaml").read_text()
        option_keys = yaml_indent2_keys(cfg, "options:")
        schema_keys = yaml_indent2_keys(cfg, "schema:")
        trans_keys = yaml_indent2_keys(trans, "configuration:")
        supplied = set(OPTIONS)
        self.assertTrue(option_keys)
        self.assertEqual(set(option_keys), set(schema_keys) & set(option_keys))
        for key in option_keys:
            self.assertIn(key, supplied)
            self.assertIn(key, trans_keys)


class Helpers(unittest.TestCase):
    def test_kid_cert(self):
        self.assertTrue(rr.kid_cert("PG-13", rr.KID_MOVIE))
        self.assertTrue(rr.kid_cert("tv-pg", rr.KID_TV))
        self.assertFalse(rr.kid_cert("R", rr.KID_MOVIE))
        self.assertFalse(rr.kid_cert("", rr.KID_MOVIE))
        self.assertFalse(rr.kid_cert("TV-MA", rr.KID_TV))

    def test_title_cert_from_nested_ratings(self):
        self.assertEqual(rr.title_cert({"certification": "PG"}), "PG")
        self.assertEqual(
            rr.title_cert({"ratings": {"tmdb": {"certification": "PG-13"}}}),
            "PG-13",
        )
        self.assertEqual(rr.title_cert({"contentRating": "TV-PG"}), "TV-PG")
        self.assertEqual(rr.title_cert({}), "")

    def test_in_root(self):
        self.assertTrue(rr.in_root("/media/Movies/Foo", "/media/Movies"))
        self.assertFalse(rr.in_root("/media/Movies Extra/Foo", "/media/Movies"))
        self.assertFalse(rr.in_root("/media/Kid Friendly Movies/Foo", "/media/Movies"))

    def test_library_dir_nested_under_media_root(self):
        old = {
            key: os.environ.get(key)
            for key in (
                "MEDIA_ROOT",
                "MEDIA_MOVIES",
                "MEDIA_MOVIES_KID",
                "MEDIA_TV",
                "MEDIA_TV_KID",
            )
        }
        try:
            os.environ["MEDIA_ROOT"] = "/media/dlna"
            os.environ["MEDIA_MOVIES"] = "Movies/Not Kid Friendly"
            os.environ["MEDIA_MOVIES_KID"] = "Movies/Kid Friendly"
            os.environ["MEDIA_TV"] = "TV/Not Kid Friendly"
            os.environ["MEDIA_TV_KID"] = "TV/Kid Friendly"
            self.assertEqual(rr.movies_dir(), "/media/dlna/Movies/Not Kid Friendly")
            self.assertEqual(rr.movies_kid_dir(), "/media/dlna/Movies/Kid Friendly")
            self.assertEqual(rr.tv_dir(), "/media/dlna/TV/Not Kid Friendly")
            self.assertEqual(rr.tv_kid_dir(), "/media/dlna/TV/Kid Friendly")
            self.assertEqual(ws.movies_dir(), "/media/dlna/Movies/Not Kid Friendly")
            os.environ["MEDIA_MOVIES"] = "../escape"
            self.assertEqual(rr.movies_dir(), "/media/dlna/Movies/Not Kid Friendly")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_history_row_empty_query_with_imdb_is_id_search(self):
        line = ws.describe_prowlarr_history_row(
            {
                "indexerId": 1,
                "eventType": "indexerQuery",
                "data": {"query": "", "queryType": "movie", "imdbId": "tt0133093", "source": "Radarr"},
            },
            {1: "YTS"},
        )
        self.assertIn("YTS", line)
        self.assertIn("IMDb tt0133093", line)
        self.assertIn("ID search", line)
        self.assertIn("Radarr", line)

    def test_history_row_accepts_pascal_case_id_keys(self):
        line = ws.describe_prowlarr_history_row(
            {
                "indexerId": 2,
                "eventType": "indexerQuery",
                "data": {"Query": "", "QueryType": "movie", "ImdbId": "tt0137523", "TmdbId": "550"},
            },
            {2: "RARBG"},
        )
        self.assertIn("IMDb tt0137523", line)
        self.assertIn("TMDb 550", line)
        self.assertIn("ID search", line)

    def test_history_row_title_query_is_named(self):
        line = ws.describe_prowlarr_history_row(
            {
                "indexerId": 3,
                "eventType": "indexerQuery",
                "data": {"query": "The Matrix", "queryType": "search"},
            },
            {3: "1337x"},
        )
        self.assertIn("q='The Matrix'", line)
        self.assertNotIn("ID search", line)

    def test_history_row_rss_has_no_title_term(self):
        line = ws.describe_prowlarr_history_row(
            {"indexerId": 4, "eventType": "indexerRss", "data": {"query": "", "queryType": "search"}},
            {4: "Nyaa"},
        )
        self.assertIn("RSS", line)
        self.assertNotIn("ID search", line)

    def test_history_row_empty_query_without_ids(self):
        line = ws.describe_prowlarr_history_row(
            {"indexerId": 5, "eventType": "indexerQuery", "data": {"query": "", "queryType": "movie"}},
            {5: "Blank"},
        )
        self.assertIn("empty query (no IDs)", line)

    def test_parse_plex(self):
        self.assertEqual(ws.parse_plex("http://172.30.32.1:32400"), ("172.30.32.1", 32400, False))
        self.assertEqual(ws.parse_plex("https://plex.example")[2], True)

    def test_pick_plex_server(self):
        servers = [
            {"owned": False, "provides": ["server"], "connection": [{"address": "1.1.1.1"}]},
            {
                "name": "Home",
                "owned": True,
                "provides": ["server"],
                "clientIdentifier": "abc",
                "connection": [{"address": "172.30.32.1"}],
            },
        ]
        chosen = ws.pick_plex_server(servers, "172.30.32.1")
        self.assertEqual(chosen["name"], "Home")

    def test_wait_page_offers_search_port_instead_of_rewriting_seerr(self):
        html = (ROOT / "pompey/rootfs/usr/share/pompey/index.html").read_text()
        self.assertIn("data.search", html)
        self.assertIn("open-search", html)
        self.assertIn("Open search", html)
        self.assertIn("Open sources", html)
        self.assertIn("open-sources-btn", html)
        self.assertIn("search_port", html)
        self.assertIn("sources_port", html)
        self.assertNotIn("location.replace", html)
        self.assertNotIn("pompey-handoff", html)
        self.assertIn('src="logo.png"', html)
        self.assertIn("setup/proton", html)
        self.assertIn("need_proton", html)
        self.assertIn("Paste the Proton WireGuard file", html)
        self.assertIn("lastSig", html)
        self.assertIn("protonSubmitted", html)
        cfg = (ROOT / "pompey/config.yaml").read_text()
        self.assertIn("5055/tcp: 5055", cfg)
        self.assertIn("9696/tcp: 9696", cfg)
        seerr = (ROOT / "pompey/rootfs/etc/services.d/seerr/run").read_text()
        self.assertIn("HOST=0.0.0.0", seerr)
        self.assertNotIn("HOST=127.0.0.1", seerr)
        docker = (ROOT / "pompey/Dockerfile").read_text()
        self.assertNotIn("plex", docker.lower())
        self.assertFalse((ROOT / "pompey/rootfs/usr/local/bin/pompey-ingress").exists())
        self.assertFalse((ROOT / "pompey/rootfs/etc/services.d/ingress-proxy").exists())
        self.assertFalse((ROOT / "tests/preview_seerr_ingress.py").exists())
        self.assertNotIn("keep_ingress_as_pompey", (BIN / "wire-stack").read_text())
        seerr_run = (ROOT / "pompey/rootfs/etc/services.d/seerr/run").read_text()
        fetch = (ROOT / "pompey/rootfs/usr/local/bin/fetch-engines").read_text()
        self.assertNotIn('touch "${POMPEY_CONFIG}/seerr/DOCKER"', seerr_run)
        self.assertNotIn('touch "${POMPEY_CONFIG}/seerr/DOCKER"', fetch)
        self.assertIn('rm -f "${POMPEY_CONFIG}/seerr/DOCKER"', seerr_run)
        self.assertIn('rm -f "${POMPEY_CONFIG}/seerr/DOCKER"', fetch)

    def test_paste_apply_does_not_echo_keys(self):
        setup = (ROOT / "pompey/rootfs/usr/local/bin/pompey-setup").read_text()
        self.assertIn("do not send the Proton key", setup)
        self.assertIn("The Proton file was saved", setup)

    def test_table_after_peer_would_fail_wg_addconf(self):
        bad = """
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 10.2.0.2/32

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:51820
Table = off
"""
        errs = wqc.check_text(bad)
        self.assertTrue(any("table" in e.lower() and "peer" in e.lower() for e in errs), errs)

    def test_table_in_interface_is_ok_for_wg_addconf(self):
        good = """
[Interface]
PrivateKey = AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Address = 10.2.0.2/32
Table = off

[Peer]
PublicKey = BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:51820
PersistentKeepalive = 25
"""
        self.assertEqual(wqc.check_text(good), [])

    def test_logs_use_clock_prefix_and_skip_status_polls(self):
        nginx = (ROOT / "pompey/rootfs/etc/nginx/nginx.conf").read_text()
        self.assertIn("if=$pompey_accesslog", nginx)
        self.assertIn("/status.json", nginx)
        setup = (ROOT / "pompey/rootfs/usr/local/bin/pompey-setup").read_text()
        status = (ROOT / "pompey/rootfs/usr/local/bin/pompey-status").read_text()
        wg = (ROOT / "pompey/rootfs/etc/services.d/wireguard/run").read_text()
        self.assertIn("%H:%M:%S", setup)
        self.assertIn("%H:%M:%S", status)
        self.assertIn("log_wg_quick", wg)

    def test_ha_store_icon_is_square_logo_is_wide(self):
        icon = ROOT / "pompey/icon.png"
        logo = ROOT / "pompey/logo.png"
        wait = ROOT / "pompey/rootfs/usr/share/pompey/logo.png"
        self.assertTrue(icon.is_file())
        self.assertTrue(logo.is_file())
        self.assertEqual(logo.read_bytes(), wait.read_bytes())
        iw, ih = png_wh(icon)
        lw, lh = png_wh(logo)
        self.assertEqual(iw, ih)
        self.assertGreater(lw, lh)

    def test_fill_fields(self):
        resource = {"fields": [{"name": "host", "value": ""}, {"name": "port", "value": 0}]}
        ws.fill_fields(resource, {"host": "127.0.0.1", "port": 8080})
        self.assertEqual(resource["fields"][0]["value"], "127.0.0.1")
        self.assertEqual(resource["fields"][1]["value"], 8080)

    def test_as_list_ignores_non_arrays(self):
        self.assertEqual(ws.as_list({"initialized": False}), [])
        self.assertEqual(ws.as_list("Ok."), [])
        self.assertEqual(ws.as_list(None), [])
        self.assertEqual(
            ws.as_list([{"hostname": "127.0.0.1"}, "skip"]),
            [{"hostname": "127.0.0.1"}],
        )
        self.assertEqual(ws.as_list({"hostname": "127.0.0.1", "name": "Radarr"})[0]["name"], "Radarr")
        self.assertEqual(ws.as_list({"results": [{"id": 1}]}), [{"id": 1}])
        self.assertEqual(ws.as_list({"message": "Sequence contains no matching element"}), [])
        self.assertEqual(ws.as_list({"name": "Unauthorized"}), [])


class WireStack(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        self.servers = []
        urls = {}
        for role in ("qbit", "sonarr", "radarr", "prowlarr", "seerr"):
            httpd, url = start_role(role, self.state)
            self.servers.append(httpd)
            urls[role] = url
        self.tmp = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-wire-{os.getpid()}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        secrets = {
            "sonarr_api_key": "sonarr-key",
            "radarr_api_key": "radarr-key",
            "prowlarr_api_key": "prowlarr-key",
            "qbit_user": "pompey",
            "qbit_password": "secret",
            "seerr_email": "pompey@local",
            "seerr_password": "seerr-secret",
        }
        secrets_path = self.tmp / "secrets.json"
        secrets_path.write_text(json.dumps(secrets))
        seerr_cfg = self.tmp / "seerr"
        seerr_cfg.mkdir(exist_ok=True)
        (seerr_cfg / "settings.json").write_text(
            json.dumps({"main": {"apiKey": "seerr-disk-key", "localLogin": True}})
        )
        nginx = self.tmp / "ingress.conf"
        ready = self.tmp / "ready"
        if ready.exists():
            for leftover in ready.iterdir():
                leftover.unlink()
        ready.mkdir(exist_ok=True)
        os.environ.update(
            {
                "POMPEY_SECRETS": str(secrets_path),
                "POMPEY_READY": str(ready),
                "MEDIA_ROOT": "/media",
                "MEDIA_MOVIES": "Movies/Not Kid Friendly",
                "MEDIA_MOVIES_KID": "Movies/Kid Friendly",
                "MEDIA_TV": "TV/Not Kid Friendly",
                "MEDIA_TV_KID": "TV/Kid Friendly",
                "PLEX_URL": "http://172.30.32.1:32400",
                "PLEX_TOKEN": "test-plex-token",
                "INDEXER_URL": "https://example-source.test",
                "INDEXER_API_KEY": "test-source-key",
                "QBIT_URL": urls["qbit"],
                "SONARR_URL": urls["sonarr"],
                "RADARR_URL": urls["radarr"],
                "PROWLARR_URL": urls["prowlarr"],
                "SEERR_URL": urls["seerr"],
                "SEERR_CONFIG": str(seerr_cfg),
                "NGINX_INGRESS_CONF": str(nginx),
                "INGRESS_PORT": "8099",
            }
        )
        self.nginx = nginx
        self.ready = ready
        self._old_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self._old_path
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()

    def test_wires_from_supplied_options(self):
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertEqual(
            set(self.state.sonarr_folders),
            {"/media/TV/Not Kid Friendly", "/media/TV/Kid Friendly"},
        )
        self.assertEqual(
            set(self.state.radarr_folders),
            {"/media/Movies/Not Kid Friendly", "/media/Movies/Kid Friendly"},
        )
        self.assertEqual(len(self.state.download_clients), 2)
        self.assertEqual({a["name"] for a in self.state.apps}, {"Sonarr", "Radarr"})
        self.assertEqual(len(self.state.indexers), 1)
        self.assertEqual(
            [c.get("name") for c in self.state.commands],
            ["ApplicationIndexerSync"],
        )
        source_fields = {f["name"]: f.get("value") for f in self.state.indexers[0]["fields"]}
        self.assertEqual(source_fields["apiKey"], "test-source-key")
        self.assertEqual(self.state.plex_auth, {"authToken": "test-plex-token"})
        self.assertEqual(self.state.seerr_radarr[0]["hostname"], "127.0.0.1")
        self.assertEqual(self.state.seerr_sonarr[0]["activeDirectory"], "/media/TV/Not Kid Friendly")
        self.assertTrue((self.ready / "seerr-arr").exists())
        self.assertTrue(self.state.initialized)
        self.assertFalse(self.nginx.exists(), "Ingress must stay the Pompey UI, not a Seerr proxy")
        live = json.loads((self.ready / "status.json").read_text())
        self.assertTrue(live["search"])
        self.assertEqual(live["search_port"], 5055)
        self.assertEqual(live["sources_port"], 9696)
        self.assertIsNone(self.state.local_auth)

    def test_wires_when_seerr_returns_objects(self):
        # Plex login creates user id 1; GET /settings/radarr can still be an object.
        self.state.seerr_object_lists = True
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertEqual(self.state.seerr_radarr[0]["hostname"], "127.0.0.1")
        self.assertEqual(self.state.seerr_sonarr[0]["hostname"], "127.0.0.1")
        self.assertTrue((self.ready / "seerr-arr").exists())
        live = json.loads((self.ready / "status.json").read_text())
        self.assertTrue(live["search"])

    def test_does_not_mark_ready_when_seerr_radarr_fails(self):
        self.state.fail_seerr_radarr = True
        with self.assertRaises(RuntimeError) as ctx:
            ws.main()
        self.assertIn("500", str(ctx.exception))
        self.assertFalse((self.ready / "wired").exists())
        self.assertTrue((self.ready / "arr-wired").exists())

    def test_does_not_mark_ready_when_source_indexer_fails(self):
        self.state.fail_indexer = True
        with self.assertRaises(RuntimeError):
            ws.main()
        self.assertFalse((self.ready / "wired").exists())
        self.assertFalse((self.ready / "arr-wired").exists())

    def test_wires_without_source_url(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertEqual(self.state.indexers, [])
        self.assertEqual(
            [c.get("name") for c in self.state.commands],
            ["ApplicationIndexerSync"],
        )

    def test_syncs_sources_already_in_prowlarr(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.indexers = [
            {
                "id": 1,
                "name": "Tracker A",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
            },
            {
                "id": 2,
                "name": "Tracker B",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": False,
                "enableInteractiveSearch": True,
            },
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual([item["name"] for item in self.state.indexers], ["Tracker A", "Tracker B"])
        tracker_b = next(item for item in self.state.indexers if item["name"] == "Tracker B")
        self.assertTrue(tracker_b["enableAutomaticSearch"])
        self.assertEqual(
            [c.get("name") for c in self.state.commands],
            ["ApplicationIndexerSync"],
        )

    def test_retries_monitored_titles_still_missing_a_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.movies = [
            {"id": 99, "title": "Waiting", "monitored": True, "hasFile": False},
            {"id": 100, "title": "Done", "monitored": True, "hasFile": True},
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        names = [c.get("name") for c in self.state.arr_commands]
        self.assertTrue(any(n.startswith("Movies") and n.endswith("Search") for n in names))
        movie_retry = next(c for c in self.state.arr_commands if str(c.get("name", "")).startswith("Movies"))
        self.assertEqual(movie_retry.get("movieIds"), [99])

    def test_logs_prowlarr_history_id_search_vs_title(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.indexers = [
            {
                "id": 1,
                "name": "YTS",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
            },
            {
                "id": 2,
                "name": "1337x",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": True,
            },
        ]
        self.state.history = [
            {
                "indexerId": 1,
                "eventType": "indexerQuery",
                "data": {
                    "query": "",
                    "queryType": "movie",
                    "imdbId": "tt0133093",
                    "source": "Radarr",
                },
            },
            {
                "indexerId": 2,
                "eventType": "indexerQuery",
                "data": {"query": "The Matrix", "queryType": "search", "source": "Radarr"},
            },
        ]
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.main()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertTrue(
            any(role == "prowlarr" and path == "/api/v1/history" for role, _method, path, _body in self.state.calls)
        )
        self.assertIn("IMDb tt0133093", out)
        self.assertIn("ID search", out)
        self.assertIn("q='The Matrix'", out)

    def test_wires_when_seerr_local_login_is_403(self):
        """Real Seerr /auth/local is login-only; API key 403s until user id 1 exists."""
        os.environ["PLEX_URL"] = ""
        os.environ["PLEX_TOKEN"] = ""
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertFalse((self.ready / "seerr-arr").exists())
        self.assertIsNone(self.state.local_auth)
        self.assertEqual(self.state.seerr_radarr, [])
        self.assertEqual(self.state.seerr_sonarr, [])
        self.assertFalse(self.state.initialized)
        live = json.loads((self.ready / "status.json").read_text())
        self.assertTrue(live["search"])

    def test_wires_arr_with_api_key_after_admin_exists(self):
        """After the wizard, the API key impersonates user id 1 with no cookie."""
        os.environ["PLEX_URL"] = ""
        os.environ["PLEX_TOKEN"] = ""
        self.state.seerr_has_admin = True
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertTrue((self.ready / "seerr-arr").exists())
        self.assertIsNone(self.state.plex_auth)
        self.assertIsNone(self.state.local_auth)
        self.assertEqual(self.state.seerr_radarr[0]["hostname"], "127.0.0.1")
        self.assertFalse(self.state.initialized)

    def test_updates_seerr_directory_when_media_folder_changes(self):
        """An existing Seerr Radarr/Sonarr row must pick up the new library folders."""
        self.state.seerr_has_admin = True
        self.state.initialized = True
        self.state.seerr_radarr = [
            {
                "id": 0,
                "name": "Radarr",
                "hostname": "127.0.0.1",
                "activeDirectory": "/media/Movies",
            }
        ]
        self.state.seerr_sonarr = [
            {
                "id": 0,
                "name": "Sonarr",
                "hostname": "127.0.0.1",
                "activeDirectory": "/media/TV",
            }
        ]
        os.environ["PLEX_URL"] = ""
        os.environ["PLEX_TOKEN"] = ""
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        os.environ["MEDIA_MOVIES"] = "Movies/Not Kid Friendly"
        os.environ["MEDIA_MOVIES_KID"] = "Movies/Kid Friendly"
        os.environ["MEDIA_TV"] = "TV/Not Kid Friendly"
        os.environ["MEDIA_TV_KID"] = "TV/Kid Friendly"
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.state.seerr_radarr[0]["activeDirectory"],
            "/media/dlna/Movies/Not Kid Friendly",
        )
        self.assertEqual(
            self.state.seerr_sonarr[0]["activeDirectory"],
            "/media/dlna/TV/Not Kid Friendly",
        )
        self.assertEqual(
            set(self.state.radarr_folders),
            {
                "/media/dlna/Movies/Not Kid Friendly",
                "/media/dlna/Movies/Kid Friendly",
            },
        )
        self.assertEqual(
            set(self.state.sonarr_folders),
            {
                "/media/dlna/TV/Not Kid Friendly",
                "/media/dlna/TV/Kid Friendly",
            },
        )
        self.assertTrue((self.ready / "seerr-arr").exists())
        radarr_puts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "PUT" and "/settings/radarr/" in call[2]
        ]
        self.assertEqual(radarr_puts[0][2], "/api/v1/settings/radarr/0")
        self.assertNotIn("id", radarr_puts[0][3] or {})
        sonarr_puts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "PUT" and "/settings/sonarr/" in call[2]
        ]
        self.assertEqual(sonarr_puts[0][2], "/api/v1/settings/sonarr/0")
        self.assertNotIn("id", sonarr_puts[0][3] or {})

    def test_marks_ready_before_wizard_without_seerr_api_key(self):
        os.environ["PLEX_URL"] = ""
        os.environ["PLEX_TOKEN"] = ""
        cfg = Path(os.environ["SEERR_CONFIG"])
        (cfg / "settings.json").unlink()
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertFalse((self.ready / "seerr-arr").exists())
        self.assertEqual(self.state.seerr_radarr, [])

    def test_does_not_mark_ready_when_initialized_seerr_cannot_wire_arr(self):
        os.environ["PLEX_URL"] = ""
        os.environ["PLEX_TOKEN"] = ""
        self.state.initialized = True
        with self.assertRaises(RuntimeError) as ctx:
            ws.main()
        self.assertIn("Radarr/Sonarr", str(ctx.exception))
        self.assertFalse((self.ready / "wired").exists())
        self.assertTrue((self.ready / "arr-wired").exists())

    def _stub_nginx(self, script: str) -> None:
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "nginx"
        stub.write_text("#!/bin/sh\n" + script)
        stub.chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{self._old_path}"

    def test_wiring_does_not_proxy_seerr_through_ingress(self):
        """A broken nginx binary must not matter: Ingress is not rewritten into Seerr."""
        self._stub_nginx("echo should-not-run >&2\nexit 1\n")
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertFalse(self.nginx.exists())


class RouteRating(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        self.servers = []
        for role, key in (("radarr", "RADARR_URL"), ("sonarr", "SONARR_URL")):
            httpd, url = start_role(role, self.state)
            self.servers.append(httpd)
            os.environ[key] = url
        os.environ["MEDIA_ROOT"] = "/media"

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()

    def test_moves_kid_unknown_stays_general(self):
        rr.route_movies("radarr-key")
        rr.route_series("sonarr-key")
        dests = {m.get("title"): m.get("rootFolderPath") for m in self.state.moved}
        self.assertEqual(dests["Kid Flick"], "/media/Movies/Kid Friendly")
        self.assertEqual(dests["Nested Kid"], "/media/Movies/Kid Friendly")
        self.assertNotIn("Unknown", dests)
        self.assertNotIn("Already Kid", dests)
        self.assertNotIn("Adult Show", dests)


class ProtonSetup(unittest.TestCase):
    def setUp(self):
        self.setup = load("pompey_setup", BIN / "pompey-setup")
        self.sample = (ROOT / "tests/fixtures/wg0.conf").read_text()

    def test_valid_proton_file(self):
        self.assertEqual(self.setup.validate_wg(self.sample), "")

    def test_empty_paste(self):
        self.assertIn("whole Proton", self.setup.validate_wg(""))

    def test_missing_endpoint(self):
        text = "\n".join(
            line for line in self.sample.splitlines() if not line.lower().startswith("endpoint")
        )
        err = self.setup.validate_wg(text)
        self.assertIn("Endpoint", err)

    def test_does_not_echo_private_key(self):
        err = self.setup.validate_wg("[Interface]\nPrivateKey = SUPERSECRET\n")
        self.assertNotIn("SUPERSECRET", err)

    def test_status_need_proton_flag(self):
        import tempfile

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_PROTON"] = "1"
        import subprocess

        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Paste the Proton WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["need_proton"])

        env.pop("POMPEY_STATUS_NEED_PROTON", None)
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "start", "Waiting for hidden engines", "65"],
            check=True,
            env=env,
        )
        stuck = json.loads((ready / "status.json").read_text())
        self.assertTrue(stuck["need_proton"])
        self.assertEqual(stuck["step"], "vpn")
        self.assertIn("Paste", stuck["label"])
        self.assertEqual(
            [item["state"] for item in stuck["steps"] if item["id"] in ("vpn", "fetch", "start")],
            ["active", "pending", "pending"],
        )

        env["POMPEY_STATUS_NEED_PROTON"] = "0"
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Bringing up the Proton tunnel", "10"],
            check=True,
            env=env,
        )
        cleared = json.loads((ready / "status.json").read_text())
        self.assertFalse(cleared["need_proton"])
        self.assertEqual(cleared["label"], "Bringing up the Proton tunnel")


class TestsNeverUseBitTorrent(unittest.TestCase):
    def test_torznab_fixture_is_gone(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())
        self.assertTrue((ROOT / "tests/lib/fake_source.py").is_file())


if __name__ == "__main__":
    unittest.main()
