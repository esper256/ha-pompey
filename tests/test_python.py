#!/usr/bin/env python3
"""HAOS is not required: supply Supervisor options.json and fake engine HTTP."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
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
        self.plex_auth: object = None
        self.seerr_radarr: list[dict] = []
        self.seerr_sonarr: list[dict] = []
        self.initialized = False
        self.qbit_prefs: object = None
        self.movies = [
            {"id": 1, "title": "Kid Flick", "certification": "PG", "path": "/media/Movies/Kid Flick"},
            {"id": 2, "title": "Unknown", "certification": "", "path": "/media/Movies/Unknown"},
            {"id": 3, "title": "Already Kid", "certification": "G", "path": "/media/Kid Friendly Movies/Already Kid"},
        ]
        self.series = [
            {"id": 10, "title": "Adult Show", "certification": "TV-MA", "path": "/media/TV/Adult Show"},
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
                    return self._send(body=state.download_clients if False else [])
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
                    state.indexers.append(body)
                    return self._send(201, body)
                return self._send(404, {"error": path})
            if role == "seerr":
                if path == "/api/v1/settings/public":
                    return self._send(body={"initialized": state.initialized})
                if path == "/api/v1/auth/plex":
                    state.plex_auth = body
                    return self._send(body={"id": 1}, cookie="connect.sid=testcookie; Path=/")
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
                    return self._send(body=state.seerr_radarr)
                if path == "/api/v1/settings/radarr" and method == "POST":
                    state.seerr_radarr.append(body)
                    return self._send(201, body)
                if path == "/api/v1/settings/sonarr" and method == "GET":
                    return self._send(body=state.seerr_sonarr)
                if path == "/api/v1/settings/sonarr" and method == "POST":
                    state.seerr_sonarr.append(body)
                    return self._send(201, body)
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

    def test_in_root(self):
        self.assertTrue(rr.in_root("/media/Movies/Foo", "/media/Movies"))
        self.assertFalse(rr.in_root("/media/Movies Extra/Foo", "/media/Movies"))
        self.assertFalse(rr.in_root("/media/Kid Friendly Movies/Foo", "/media/Movies"))

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

    def test_wait_page_reloads_only_when_search_is_live(self):
        html = (ROOT / "pompey/rootfs/usr/share/pompey/index.html").read_text()
        self.assertIn("data.search", html)
        self.assertIn("location.replace", html)
        self.assertIn("Opening search", html)

    def test_fill_fields(self):
        resource = {"fields": [{"name": "host", "value": ""}, {"name": "port", "value": 0}]}
        ws.fill_fields(resource, {"host": "127.0.0.1", "port": 8080})
        self.assertEqual(resource["fields"][0]["value"], "127.0.0.1")
        self.assertEqual(resource["fields"][1]["value"], 8080)


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
        }
        secrets_path = self.tmp / "secrets.json"
        secrets_path.write_text(json.dumps(secrets))
        nginx = self.tmp / "ingress.conf"
        ready = self.tmp / "ready"
        ready.mkdir(exist_ok=True)
        os.environ.update(
            {
                "POMPEY_SECRETS": str(secrets_path),
                "POMPEY_READY": str(ready),
                "MEDIA_ROOT": "/media",
                "PLEX_URL": OPTIONS["plex_url"],
                "PLEX_TOKEN": OPTIONS["plex_token"],
                "INDEXER_URL": OPTIONS["indexer_url"],
                "INDEXER_API_KEY": OPTIONS["indexer_api_key"],
                "QBIT_URL": urls["qbit"],
                "SONARR_URL": urls["sonarr"],
                "RADARR_URL": urls["radarr"],
                "PROWLARR_URL": urls["prowlarr"],
                "SEERR_URL": urls["seerr"],
                "NGINX_INGRESS_CONF": str(nginx),
                "INGRESS_PORT": "8099",
            }
        )
        self.nginx = nginx
        self.ready = ready

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()

    def test_wires_from_supplied_options(self):
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertEqual(
            set(self.state.sonarr_folders),
            {"/media/TV", "/media/Kid Friendly TV"},
        )
        self.assertEqual(
            set(self.state.radarr_folders),
            {"/media/Movies", "/media/Kid Friendly Movies"},
        )
        self.assertEqual(len(self.state.download_clients), 2)
        self.assertEqual({a["name"] for a in self.state.apps}, {"Sonarr", "Radarr"})
        self.assertEqual(len(self.state.indexers), 1)
        source_fields = {f["name"]: f.get("value") for f in self.state.indexers[0]["fields"]}
        self.assertEqual(source_fields["apiKey"], "test-source-key")
        self.assertEqual(self.state.plex_auth, {"authToken": "test-plex-token"})
        self.assertEqual(self.state.seerr_radarr[0]["hostname"], "127.0.0.1")
        self.assertEqual(self.state.seerr_sonarr[0]["activeDirectory"], "/media/TV")
        self.assertTrue(self.state.initialized)
        text = self.nginx.read_text()
        self.assertIn("proxy_pass " + os.environ["SEERR_URL"], text)
        self.assertIn("X-Forwarded-Prefix", text)
        self.assertIn("status.json", text)
        self.assertNotIn("test-plex-token", text)
        live = json.loads((self.ready / "status.json").read_text())
        self.assertTrue(live["search"])
        self.assertTrue(live["handoff"])


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
        self.assertEqual(dests["Kid Flick"], "/media/Kid Friendly Movies")
        self.assertNotIn("Unknown", dests)
        self.assertNotIn("Already Kid", dests)
        self.assertNotIn("Adult Show", dests)


class TestsNeverUseBitTorrent(unittest.TestCase):
    def test_torznab_fixture_is_gone(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())


if __name__ == "__main__":
    unittest.main()
