#!/usr/bin/env python3
"""HAOS is not required: supply Supervisor options.json and fake engine HTTP."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import datetime
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
arrp = load("prowlarr_arr_proxy", BIN / "prowlarr-arr-proxy")
rr = load("route_rating", BIN / "route-rating")
wqc = load("wg_quick_contract", ROOT / "tests/lib/wg_quick_contract.py")
emitmod = load("pompey_log_emit", BIN / "pompey-log-emit")
vpnstats = load("pompey_vpn_stats", BIN / "pompey-vpn-stats")


def any_quality_bundle(profile_id: int = 1, name: str = "Any") -> dict:
    names = [
        "CAM",
        "TELESYNC",
        "WORKPRINT",
        "DVD",
        "WEBDL-480p",
        "HDTV-720p",
        "WEBDL-720p",
        "WEBRip-720p",
        "Bluray-720p",
        "HDTV-1080p",
        "WEBDL-1080p",
        "WEBRip-1080p",
        "Bluray-1080p",
        "Remux-1080p",
        "HDTV-2160p",
        "WEBDL-2160p",
        "WEBRip-2160p",
        "Bluray-2160p",
        "Remux-2160p",
        "BR-DISK",
    ]
    items = []
    defs = []
    remux_4k_id = None
    for i, n in enumerate(names, start=1):
        q = {"id": i, "name": n}
        items.append({"quality": q, "items": [], "allowed": True})
        defs.append(
            {
                "id": i,
                "quality": q,
                "title": n,
                "minSize": 0,
                "preferredSize": 199,
                "maxSize": 400,
            }
        )
        if n == "Remux-2160p":
            remux_4k_id = i
    return {
        "profile": {
            "id": profile_id,
            "name": name,
            "upgradeAllowed": True,
            "cutoff": remux_4k_id or items[-1]["quality"]["id"],
            "items": items,
            "minFormatScore": 0,
            "cutoffFormatScore": 0,
            "formatItems": [],
        },
        "definitions": defs,
    }


def allowed_quality_names(profile: dict) -> tuple[set[str], set[str]]:
    allowed: set[str] = set()
    blocked: set[str] = set()

    def walk(node: dict) -> None:
        q = node.get("quality") if isinstance(node.get("quality"), dict) else {}
        name = q.get("name")
        if name:
            (allowed if node.get("allowed") else blocked).add(name)
        for child in node.get("items") or []:
            if isinstance(child, dict):
                walk(child)

    for item in profile.get("items") or []:
        walk(item)
    return allowed, blocked


def profile_named(profiles: list[dict], name: str) -> dict:
    return next(item for item in profiles if item.get("name") == name)


def quality_profile_reject(body: dict, formats: list[dict], state: "FakeState") -> str | None:
    """Mimic Radarr QualityProfileController validators that 0.2.28 tripped over."""
    if state.fail_quality_profiles:
        return "quality profiles disabled for test"
    name = (body or {}).get("name")
    if name in state.reject_profile_names:
        return "Cutoff must be an allowed quality or group"
    if not state.strict_quality_profiles:
        return None
    if not isinstance(body, dict) or not str(body.get("name") or "").strip():
        return "Name: not empty"
    try:
        min_up = int(body.get("minUpgradeFormatScore") or 0)
    except (TypeError, ValueError):
        min_up = 0
    if min_up < 1:
        return "MinUpgradeFormatScore: must be greater than or equal to 1"
    items = body.get("items") or []
    cutoff = body.get("cutoff")
    cutoff_ok = False
    seen: list[str] = []
    group_ids: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = item.get("quality") if isinstance(item.get("quality"), dict) else None
        if q and q.get("name"):
            seen.append(str(q["name"]))
            if item.get("allowed") and q.get("id") == cutoff:
                cutoff_ok = True
            continue
        gid = item.get("id")
        if gid in (None, 0):
            return "Groups must have an ID"
        if gid in group_ids:
            return "Groups must have a unique ID"
        group_ids.append(gid)
        if item.get("allowed") and gid == cutoff:
            cutoff_ok = True
        for child in item.get("items") or []:
            if not isinstance(child, dict):
                continue
            cq = child.get("quality") if isinstance(child.get("quality"), dict) else None
            if cq and cq.get("name"):
                seen.append(str(cq["name"]))
    if cutoff is not None and not cutoff_ok:
        return "Cutoff must be an allowed quality or group"
    missing = [n for n in state.required_quality_names if n not in seen]
    if missing:
        return "Items: Must contain all qualities"
    fmt_ids = {int(fmt["id"]) for fmt in formats if fmt.get("id") is not None}
    have = {
        int(item.get("format"))
        for item in (body.get("formatItems") or [])
        if item.get("format") is not None
    }
    if fmt_ids != have:
        return (
            "All Custom Formats and no extra ones need to be present inside your Profile! "
            "Try refreshing your browser."
        )
    return None


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
        self.radarr_clients: list[dict] = []
        self.sonarr_clients: list[dict] = []
        radarr_q = any_quality_bundle(1, "Any")
        sonarr_q = any_quality_bundle(1, "Any")
        self.radarr_profiles: list[dict] = [radarr_q["profile"]]
        self.sonarr_profiles: list[dict] = [sonarr_q["profile"]]
        self.radarr_defs: list[dict] = radarr_q["definitions"]
        self.sonarr_defs: list[dict] = sonarr_q["definitions"]
        self.radarr_formats: list[dict] = []
        self.sonarr_formats: list[dict] = []
        self.required_quality_names = {
            item["quality"]["name"]
            for item in radarr_q["profile"]["items"]
            if isinstance(item.get("quality"), dict) and item["quality"].get("name")
        }
        self.strict_quality_profiles = True
        self.fail_quality_profiles = False
        self.reject_profile_names: set[str] = set()
        self.quality_post_empty_body = False
        self.fail_media_management = False
        self.apps: list[dict] = []
        self.indexers: list[dict] = []
        self.radarr_indexers: list[dict] = []
        self.sonarr_indexers: list[dict] = []
        self.commands: list[dict] = []
        self.arr_commands: list[dict] = []
        self.radarr_command_queue: list[dict] = []
        self.sonarr_command_queue: list[dict] = []
        self.radarr_media: dict = {
            "id": 1,
            "enableCompletedDownloadHandling": True,
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 100,
            "copyUsingHardlinks": True,
        }
        self.sonarr_media: dict = {
            "id": 1,
            "enableCompletedDownloadHandling": True,
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 100,
            "copyUsingHardlinks": True,
        }
        self.radarr_dl_config: dict = {
            "id": 1,
            "enableCompletedDownloadHandling": True,
            "autoRedownloadFailed": True,
            "autoRedownloadFailedFromInteractiveSearch": True,
        }
        self.sonarr_dl_config: dict = {
            "id": 1,
            "enableCompletedDownloadHandling": True,
            "autoRedownloadFailed": True,
            "autoRedownloadFailedFromInteractiveSearch": True,
        }
        self.manual_import: list[dict] = []
        self.sonarr_manual_import: list[dict] = []
        self.episodes: list[dict] = []
        self.wanted_missing: list[dict] = []
        self.queue: list[dict] = []
        self.sonarr_queue: list[dict] = []
        self.qbit_categories: dict = {}
        self.qbit_torrents: list[dict] = []
        self.qbit_removed: list[dict] = []
        self.qbit_stopped: list[dict] = []
        self.history: list[dict] = []
        self.plex_auth: object = None
        self.local_auth: object = None
        self.allow_seerr_local = False
        self.seerr_object_lists = False
        self.seerr_has_admin = False
        self.seerr_radarr: list[dict] = []
        self.seerr_sonarr: list[dict] = []
        self.seerr_users: list[dict] = []
        self.seerr_jobs: list[str] = []
        self.seerr_main: object = None
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
            {
                "id": 11,
                "title": "Kid Show",
                "certification": "TV-PG",
                "path": "/media/TV/Not Kid Friendly/Kid Show",
            },
            {
                "id": 12,
                "title": "Kid Pathless",
                "certification": "TV-Y",
                "rootFolderPath": "/media/TV/Not Kid Friendly/Kid Pathless",
            },
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

        def _qbit_hashes(self, body) -> str:
            form = body if isinstance(body, dict) else {}
            hashes = form.get("hashes") or form.get("hash") or ""
            if isinstance(hashes, list):
                hashes = hashes[0] if hashes else ""
            return str(hashes)

        def _stop_qbit(self, body, action: str):
            hashes = self._qbit_hashes(body)
            state.qbit_stopped.append({"hashes": hashes, "action": action})
            drop = {part for part in hashes.split("|") if part}
            for item in state.qbit_torrents:
                if item.get("hash") in drop:
                    item["state"] = "stoppedUP"
            return self._send(text="Ok.")

        def _handle(self, method: str):
            role = getattr(self.server, "role")
            raw_path = self.path
            path = raw_path.split("?", 1)[0]
            query = parse_qs(raw_path.split("?", 1)[1]) if "?" in raw_path else {}
            body = self._read() if method in {"POST", "PUT"} else None
            state.calls.append((role, method, path, body))
            if role == "qbit":
                if path == "/api/v2/app/version":
                    return self._send(text="5.0.4")
                if path == "/api/v2/torrents/" + "info" and method == "GET":
                    return self._send(body=state.qbit_torrents)
                if path == "/api/v2/torrents/" + "stop" and method == "POST":
                    return self._stop_qbit(body, "stop")
                if path == "/api/v2/torrents/" + "pause" and method == "POST":
                    return self._stop_qbit(body, "pause")
                if path == "/api/v2/torrents/delete" and method == "POST":
                    form = body if isinstance(body, dict) else {}
                    hashes = form.get("hashes") or form.get("hash") or ""
                    if isinstance(hashes, list):
                        hashes = hashes[0] if hashes else ""
                    delete_files = form.get("deleteFiles")
                    if isinstance(delete_files, list):
                        delete_files = delete_files[0] if delete_files else ""
                    state.qbit_removed.append(
                        {"hashes": str(hashes), "deleteFiles": str(delete_files)}
                    )
                    drop = {part for part in str(hashes).split("|") if part}
                    state.qbit_torrents = [
                        item for item in state.qbit_torrents if item.get("hash") not in drop
                    ]
                    return self._send(text="Ok.")
                if path == "/api/v2/torrents/createCategory":
                    form = body if isinstance(body, dict) else {}
                    name = (form.get("category") or [""])[0] if isinstance(form.get("category"), list) else form.get("category")
                    save = (form.get("savePath") or [""])[0] if isinstance(form.get("savePath"), list) else form.get("savePath")
                    if name:
                        state.qbit_categories[str(name)] = str(save or "")
                    return self._send(text="Ok.")
                if path == "/api/v2/torrents/editCategory":
                    form = body if isinstance(body, dict) else {}
                    name = (form.get("category") or [""])[0] if isinstance(form.get("category"), list) else form.get("category")
                    save = (form.get("savePath") or [""])[0] if isinstance(form.get("savePath"), list) else form.get("savePath")
                    if name:
                        state.qbit_categories[str(name)] = str(save or "")
                    return self._send(text="Ok.")
                if path == "/api/v2/auth/login":
                    return self._send(text="Ok.")
                if path == "/api/v2/app/setPreferences":
                    state.qbit_prefs = body
                    return self._send(text="Ok.")
                return self._send(404, {"error": path})
            if role in {"sonarr", "radarr"}:
                folders = state.sonarr_folders if role == "sonarr" else state.radarr_folders
                profiles = state.radarr_profiles if role == "radarr" else state.sonarr_profiles
                defs = state.radarr_defs if role == "radarr" else state.sonarr_defs
                formats = state.radarr_formats if role == "radarr" else state.sonarr_formats
                if path == "/ping":
                    return self._send(body={"status": "OK"})
                if path.endswith("/rootfolder") and method == "GET":
                    return self._send(body=[{"path": p} for p in folders])
                if path.endswith("/rootfolder") and method == "POST":
                    folders.append((body or {}).get("path"))
                    return self._send(201, body)
                if path.endswith("/config/mediamanagement") and method == "GET":
                    media = state.radarr_media if role == "radarr" else state.sonarr_media
                    return self._send(body=media)
                if "/config/mediamanagement/" in path and method == "PUT":
                    saved = dict(body or {})
                    try:
                        min_free = int(saved.get("minimumFreeSpaceWhenImporting") or 0)
                    except (TypeError, ValueError):
                        min_free = 0
                    if state.fail_media_management or min_free < 100:
                        return self._send(
                            400,
                            [
                                {
                                    "propertyName": "MinimumFreeSpaceWhenImporting",
                                    "errorMessage": (
                                        "'Minimum Free Space When Importing' "
                                        "must be greater than or equal to '100'."
                                    ),
                                }
                            ],
                        )
                    if role == "radarr":
                        state.radarr_media = saved
                    else:
                        state.sonarr_media = saved
                    return self._send(body=saved)
                if path.endswith("/queue") and method == "GET":
                    rows = state.queue if role == "radarr" else state.sonarr_queue
                    return self._send(body={"records": rows, "page": 1, "pageSize": 50})
                if path.endswith("/config/downloadclient") and method == "GET":
                    cfg = state.radarr_dl_config if role == "radarr" else state.sonarr_dl_config
                    return self._send(body=cfg)
                if "/config/downloadclient/" in path and method == "PUT":
                    saved = dict(body or {})
                    if role == "radarr":
                        state.radarr_dl_config = saved
                    else:
                        state.sonarr_dl_config = saved
                    return self._send(body=saved)
                if path.endswith("/manualimport") and method == "GET":
                    folder = (query.get("folder") or [""])[0]
                    pool = (
                        state.manual_import
                        if role == "radarr"
                        else state.sonarr_manual_import
                    )
                    items = []
                    for item in pool:
                        item_path = str(item.get("path") or "")
                        if folder and folder not in item_path and not item_path.startswith(folder):
                            continue
                        items.append(item)
                    return self._send(body=items)
                if path.endswith("/downloadclient") and method == "GET":
                    clients = state.radarr_clients if role == "radarr" else state.sonarr_clients
                    return self._send(body=clients)
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
                    clients = state.radarr_clients if role == "radarr" else state.sonarr_clients
                    posted = dict(body or {})
                    posted.setdefault("id", len(clients) + 1)
                    clients.append(posted)
                    state.download_clients.append(posted)
                    return self._send(201, posted)
                if "/downloadclient/" in path and method == "PUT":
                    clients = state.radarr_clients if role == "radarr" else state.sonarr_clients
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(clients):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            clients[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
                if path.endswith("/qualityprofile/schema"):
                    schema = json.loads(json.dumps(profiles[0])) if profiles else {"items": []}
                    schema.pop("id", None)
                    schema["name"] = ""
                    return self._send(body=schema)
                if path.endswith("/qualityprofile") and method == "GET":
                    return self._send(body=profiles)
                if path.endswith("/qualityprofile") and method == "POST":
                    posted = dict(body or {})
                    err = quality_profile_reject(posted, formats, state)
                    if err:
                        return self._send(400, {"message": err})
                    next_id = max((int(p.get("id") or 0) for p in profiles), default=0) + 1
                    posted["id"] = next_id
                    profiles.append(posted)
                    if state.quality_post_empty_body:
                        return self._send(201)
                    return self._send(201, posted)
                if "/qualityprofile/" in path and method == "DELETE":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(profiles):
                        if item.get("id") == idx:
                            profiles.pop(i)
                            return self._send(200, {})
                    return self._send(404, {"error": path})
                if "/qualityprofile/" in path and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    err = quality_profile_reject(body or {}, formats, state)
                    if err:
                        return self._send(400, {"message": err})
                    for i, item in enumerate(profiles):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            profiles[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
                if path.endswith("/customformat") and method == "GET":
                    return self._send(body=formats)
                if path.endswith("/customformat") and method == "POST":
                    posted = dict(body or {})
                    posted["id"] = len(formats) + 1
                    formats.append(posted)
                    return self._send(201, posted)
                if "/customformat/" in path and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(formats):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            formats[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
                if path.endswith("/qualitydefinition") and method == "GET":
                    return self._send(body=defs)
                if "/qualitydefinition/" in path and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(defs):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            defs[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
                if path.endswith("/languageprofile"):
                    return self._send(body=[])
                if path.endswith("/movie") and method == "GET":
                    return self._send(body=state.movies)
                if "/movie/" in path and method == "GET":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for movie in state.movies:
                        if movie.get("id") == idx:
                            return self._send(body=movie)
                    return self._send(404, {"error": path})
                if "/movie/" in path and method == "PUT":
                    state.moved.append(body)
                    return self._send(body=body)
                if path.endswith("/series") and method == "GET":
                    return self._send(body=state.series)
                if path.endswith("/episode") and method == "GET":
                    series_id = (query.get("seriesId") or [""])[0]
                    rows = state.episodes
                    if series_id:
                        rows = [
                            ep
                            for ep in rows
                            if str(ep.get("seriesId")) == str(series_id)
                        ]
                    return self._send(body=rows)
                if path.endswith("/wanted/missing") and method == "GET":
                    rows = state.wanted_missing
                    return self._send(
                        body={
                            "records": rows,
                            "page": 1,
                            "pageSize": 20,
                            "totalRecords": len(rows),
                        }
                    )
                if "/series/" in path and method == "GET":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for show in state.series:
                        if show.get("id") == idx:
                            return self._send(body=show)
                    return self._send(404, {"error": path})
                if "/series/" in path and method == "PUT":
                    state.moved.append(body)
                    return self._send(body=body)
                if path.endswith("/command") and method == "GET":
                    queued = (
                        state.radarr_command_queue
                        if role == "radarr"
                        else state.sonarr_command_queue
                    )
                    return self._send(body=queued)
                if path.endswith("/command") and method == "POST":
                    state.arr_commands.append(body or {})
                    return self._send(201, body or {})
                if path.endswith("/indexer") and method == "GET":
                    listed = state.radarr_indexers if role == "radarr" else state.sonarr_indexers
                    return self._send(body=listed)
                return self._send(404, {"error": path})
            if role == "prowlarr":
                if path == "/ping":
                    return self._send(body={"status": "OK"})
                if path == "/api/v1/applications" and method == "GET":
                    return self._send(body=state.apps)
                if path == "/api/v1/applications/schema":
                    fields = [{"name": n} for n in ("prowlarrUrl", "baseUrl", "apiKey", "syncCategories")]
                    return self._send(
                        body=[
                            {"implementation": "Sonarr", "fields": json.loads(json.dumps(fields))},
                            {"implementation": "Radarr", "fields": json.loads(json.dumps(fields))},
                        ]
                    )
                if path == "/api/v1/applications" and method == "POST":
                    posted = dict(body or {})
                    if posted.get("id") is None:
                        posted["id"] = len(state.apps) + 1
                    state.apps.append(posted)
                    return self._send(201, posted)
                if path.startswith("/api/v1/applications/") and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(state.apps):
                        if item.get("id") == idx:
                            state.apps[i] = body or item
                            if isinstance(state.apps[i], dict):
                                state.apps[i]["id"] = idx
                            return self._send(body=state.apps[i])
                    return self._send(404, {"error": path})
                if path == "/api/v1/indexer" and method == "GET":
                    return self._send(body=state.indexers)
                if path == "/api/v1/indexer/schema":
                    fields = [{"name": n} for n in ("baseUrl", "apiPath", "apiKey")]
                    return self._send(body=[{"implementation": "Torznab", "fields": fields}])
                if path.startswith("/api/v1/indexer/") and method == "GET":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for item in state.indexers:
                        if item.get("id") == idx:
                            return self._send(body=item)
                    return self._send(404, {"error": path})
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
                    state.seerr_main = body
                    return self._send(body=body)
                if path == "/api/v1/user" and method == "GET":
                    return self._send(body={"page": 1, "results": state.seerr_users})
                if path.startswith("/api/v1/user/") and method == "PUT":
                    try:
                        uid = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(state.seerr_users):
                        if item.get("id") == uid:
                            saved = dict(item)
                            saved.update(body or {})
                            saved["id"] = uid
                            state.seerr_users[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
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
                if path.startswith("/api/v1/settings/jobs/") and path.endswith("/run"):
                    state.seerr_jobs.append(path.rsplit("/", 2)[-2])
                    return self._send(body={"ok": True})
                return self._send(404, {"error": path})
            return self._send(500, {"error": "no role"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

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

    def test_history_row_top100_is_browse_not_id_search(self):
        line = ws.describe_prowlarr_history_row(
            {
                "indexerId": 6,
                "eventType": "indexerQuery",
                "data": {
                    "query": "",
                    "queryType": "movie",
                    "url": "https://tracker.example/top100",
                    "source": "Radarr",
                },
            },
            {6: "RARBG"},
        )
        self.assertIn("browse/top100", line)
        self.assertIn("not the Seerr title", line)
        self.assertNotIn("ID search", line)

    def test_caps_xml_drops_id_params_keeps_title_and_tv_season(self):
        xml = """
        <caps>
          <searching>
            <search available="yes" supportedParams="q"/>
            <tv-search available="yes" supportedParams="q,season,ep,imdbid,tmdbid,tvdbid"/>
            <movie-search available="yes" supportedParams="q,imdbid,tmdbid"/>
          </searching>
        </caps>
        """
        out = arrp.rewrite_caps_xml(xml)
        self.assertIn('supportedParams="q,season,ep"', out)
        self.assertIn('<movie-search available="yes" supportedParams="q"/>', out)
        self.assertNotIn("imdbid", out)
        self.assertNotIn("tmdbid", out)
        self.assertNotIn("tvdbid", out)

    def test_caps_xml_inserts_q_if_only_ids_were_advertised(self):
        xml = '<movie-search available="yes" supportedParams="imdbid,tmdbid"/>'
        self.assertEqual(
            arrp.rewrite_caps_xml(xml),
            '<movie-search available="yes" supportedParams="q"/>',
        )

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
        self.assertIn("vpn-bw", html)
        self.assertIn("renderVpn", html)
        self.assertIn("vpn-graph", html)
        self.assertIn("data.vpn", html)
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

    def test_wire_keeps_housekeeping_hidden_qbit(self):
        wire = (ROOT / "pompey/rootfs/etc/services.d/wire/run").read_text()
        self.assertIn("housekeep", wire)
        src = (ROOT / "pompey/rootfs/usr/local/bin/wire-stack").read_text()
        self.assertIn('"deleteFiles": "false"', src)
        self.assertNotIn('"deleteFiles": "true"', src)

    def test_download_scan_paths_include_legacy_category_folders(self):
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.assertEqual(
            ws.download_scan_paths(),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/radarr",
                "/media/dlna/downloads/complete/sonarr",
            ],
        )
        self.assertEqual(
            ws.download_scan_paths("radarr"),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/radarr",
            ],
        )
        self.assertEqual(
            ws.download_scan_paths("sonarr"),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/sonarr",
            ],
        )
        os.environ.pop("MEDIA_ROOT", None)

    def test_existing_download_scan_paths_skip_missing_folders(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            complete = base / "downloads" / "complete"
            complete.mkdir(parents=True)
            (complete / "radarr").mkdir()
            os.environ["MEDIA_ROOT"] = str(base)
            try:
                self.assertEqual(
                    ws.existing_download_scan_paths("radarr"),
                    [str(complete), str(complete / "radarr")],
                )
                self.assertEqual(
                    ws.existing_download_scan_paths("sonarr"),
                    [str(complete)],
                )
            finally:
                os.environ.pop("MEDIA_ROOT", None)

    def test_fill_fields(self):
        resource = {"fields": [{"name": "host", "value": ""}, {"name": "port", "value": 0}]}
        ws.fill_fields(resource, {"host": "127.0.0.1", "port": 8080})
        self.assertEqual(resource["fields"][0]["value"], "127.0.0.1")
        self.assertEqual(resource["fields"][1]["value"], 8080)

    def test_set_app_fields_appends_missing(self):
        resource = {"fields": [{"name": "prowlarrUrl", "value": "http://127.0.0.1:9696"}]}
        ws.set_app_fields(resource, {"prowlarrUrl": "http://127.0.0.1:9698", "syncCategories": [2000]})
        fields = {f["name"]: f.get("value") for f in resource["fields"]}
        self.assertEqual(fields["prowlarrUrl"], "http://127.0.0.1:9698")
        self.assertEqual(fields["syncCategories"], [2000])

    def test_after_download_defaults_to_stop_sharing(self):
        os.environ.pop("AFTER_DOWNLOAD", None)
        self.assertEqual(ws.after_download(), "stop_sharing")
        os.environ["AFTER_DOWNLOAD"] = "share_to_ratio"
        self.assertEqual(ws.after_download(), "share_to_ratio")
        os.environ["AFTER_DOWNLOAD"] = "share-one-day"
        self.assertEqual(ws.after_download(), "share_one_day")
        os.environ.pop("AFTER_DOWNLOAD", None)

    def test_language_options_defaults(self):
        for key in ("PREFERRED_LANGUAGE", "ANIME_AUDIO", "SUBTITLES"):
            os.environ.pop(key, None)
        self.assertEqual(ws.preferred_language(), "english")
        self.assertEqual(ws.anime_audio(), "dual_audio")
        self.assertEqual(ws.subtitles_pref(), "english")
        os.environ["PREFERRED_LANGUAGE"] = "original"
        os.environ["ANIME_AUDIO"] = "english"
        os.environ["SUBTITLES"] = "none"
        self.assertEqual(ws.preferred_language(), "original")
        self.assertEqual(ws.anime_audio(), "english")
        self.assertEqual(ws.subtitles_pref(), "none")
        for key in ("PREFERRED_LANGUAGE", "ANIME_AUDIO", "SUBTITLES"):
            os.environ.pop(key, None)

    def test_qbit_forgets_missing_files_not_active_downloads(self):
        self.assertTrue(
            ws.qbit_should_forget({"hash": "aa", "state": "missingFiles", "progress": 1})
        )
        self.assertFalse(
            ws.qbit_should_forget(
                {"hash": "bb", "state": "downloading", "progress": 0.4}, gone=False
            )
        )
        self.assertFalse(
            ws.qbit_should_forget(
                {"hash": "cc", "state": "moving", "progress": 1}, gone=True
            )
        )
        self.assertTrue(
            ws.qbit_should_forget(
                {"hash": "dd", "state": "uploading", "progress": 1, "amount_left": 0},
                gone=True,
            )
        )
        self.assertFalse(
            ws.qbit_should_forget(
                {"hash": "ee", "state": "uploading", "progress": 1}, gone=False
            )
        )

    def test_qbit_unlocks_finished_files_still_in_complete(self):
        root = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-unlock-{os.getpid()}"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        payload = complete / "Title.mkv"
        payload.write_text("x")
        incomplete = root / "downloads" / "incomplete" / "Title.mkv"
        incomplete.parent.mkdir(parents=True)
        incomplete.write_text("y")
        os.environ["MEDIA_ROOT"] = str(root)
        try:
            self.assertTrue(
                ws.qbit_should_unlock(
                    {
                        "hash": "ff",
                        "state": "uploading",
                        "progress": 1,
                        "amount_left": 0,
                        "content_path": str(payload),
                    }
                )
            )
            self.assertFalse(
                ws.qbit_should_unlock(
                    {
                        "hash": "gg",
                        "state": "stoppedUP",
                        "progress": 1,
                        "amount_left": 0,
                        "content_path": str(payload),
                    }
                )
            )
            self.assertFalse(
                ws.qbit_should_unlock(
                    {
                        "hash": "hh",
                        "state": "downloading",
                        "progress": 0.4,
                        "content_path": str(payload),
                    }
                )
            )
            self.assertFalse(
                ws.qbit_should_unlock(
                    {
                        "hash": "ii",
                        "state": "uploading",
                        "progress": 1,
                        "amount_left": 0,
                        "content_path": str(incomplete),
                    }
                )
            )
        finally:
            os.environ.pop("MEDIA_ROOT", None)
            payload.unlink(missing_ok=True)
            incomplete.unlink(missing_ok=True)
            for path in (complete, incomplete.parent, root / "downloads", root):
                try:
                    path.rmdir()
                except OSError:
                    pass

    def test_manual_import_file_requires_match(self):
        quality = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}}
        movie = {
            "path": "/media/dlna/downloads/complete/ok.mkv",
            "movieId": 9,
            "quality": quality,
            "languages": [{"id": 1, "name": "English"}],
        }
        self.assertEqual(ws.manual_import_file(movie, "radarr")["movieId"], 9)
        self.assertEqual(
            ws.arr_import_destination(
                {
                    **movie,
                    "movie": {
                        "id": 9,
                        "path": "/media/dlna/Movies/Not Kid Friendly/Ok (2024)",
                    },
                },
                "radarr",
            ),
            "/media/dlna/Movies/Not Kid Friendly/Ok (2024)",
        )
        self.assertIsNone(
            ws.manual_import_file({**movie, "movieId": None, "movie": {}}, "radarr")
        )
        episode = {
            "path": "/media/dlna/downloads/complete/ep.mkv",
            "seriesId": 3,
            "episodeIds": [11, 12],
            "quality": quality,
        }
        self.assertEqual(ws.episode_ids_of(episode), [11, 12])
        self.assertEqual(
            ws.episode_ids_of({"episodes": [{"id": 11}, {"id": 12, "hasFile": False}]}),
            [11, 12],
        )
        self.assertEqual(ws.manual_import_file(episode, "sonarr")["episodeIds"], [11, 12])
        nested_only = {
            "path": "/media/dlna/downloads/complete/ep.mkv",
            "series": {"id": 3},
            "episodes": [{"id": 11}, {"id": 12}],
            "quality": quality,
        }
        self.assertEqual(ws.manual_import_file(nested_only, "sonarr")["seriesId"], 3)
        self.assertEqual(ws.manual_import_file(nested_only, "sonarr")["episodeIds"], [11, 12])
        self.assertTrue(
            ws.arr_already_has_library_file(
                {"movie": {"hasFile": True, "path": "/media/dlna/Movies/Not Kid Friendly/Ok (2024)"}},
                "radarr",
            )
        )
        self.assertFalse(
            ws.arr_already_has_library_file({"movie": {"hasFile": False}}, "radarr")
        )
        self.assertTrue(
            ws.arr_already_has_library_file(
                {"episodes": [{"id": 11, "hasFile": True}, {"id": 12, "hasFile": True}]},
                "sonarr",
            )
        )
        self.assertFalse(
            ws.arr_already_has_library_file(
                {"episodes": [{"id": 11, "hasFile": True}, {"id": 12, "hasFile": False}]},
                "sonarr",
            )
        )
        self.assertTrue(ws.is_expected_cross_kind_reject("Unknown Series"))
        self.assertTrue(ws.is_expected_cross_kind_reject("Unknown Movie"))
        self.assertFalse(ws.is_expected_cross_kind_reject("Not a wanted quality"))
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.assertEqual(
            ws.release_dir_under_complete(
                "/media/dlna/downloads/complete/www.UIndex.org - Title/file.mp4"
            ),
            "/media/dlna/downloads/complete/www.UIndex.org - Title",
        )
        self.assertEqual(
            ws.release_dir_under_complete("/media/dlna/downloads/complete/file.mp4"),
            "",
        )
        os.environ.pop("MEDIA_ROOT", None)
        self.assertTrue(
            ws.title_needs_library_file(
                {"title": "Waiting", "monitored": True, "hasFile": False},
                "radarr",
            )
        )
        self.assertFalse(
            ws.title_needs_library_file(
                {"title": "Done", "monitored": True, "hasFile": True},
                "radarr",
            )
        )
        self.assertTrue(
            ws.title_needs_library_file(
                {
                    "title": "Partial",
                    "monitored": True,
                    "statistics": {"episodeFileCount": 3, "episodeCount": 8},
                    "path": "/media/dlna/TV/Not Kid Friendly/Partial",
                },
                "sonarr",
            )
        )
        self.assertFalse(
            ws.title_needs_library_file(
                {
                    "title": "Caught up",
                    "monitored": True,
                    "statistics": {"episodeFileCount": 8, "episodeCount": 8},
                },
                "sonarr",
            )
        )
        self.assertFalse(
            ws.title_needs_library_file(
                {
                    "title": "Unmonitored",
                    "monitored": False,
                    "statistics": {"episodeFileCount": 0, "episodeCount": 8},
                },
                "sonarr",
            )
        )
        now = datetime.datetime(2026, 8, 29, 14, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(ws.age_label("2026-08-29T13:46:00Z", now), "14m")
        self.assertEqual(
            ws.summarize_arr_commands(
                [
                    {
                        "name": "EpisodeSearch",
                        "status": "started",
                        "started": "2026-08-29T13:46:00Z",
                        "priority": "low",
                        "body": {"seriesTitle": "Show", "episodeIds": [4]},
                    },
                    {
                        "name": "EpisodeSearch",
                        "status": "queued",
                        "queued": "2026-08-29T13:50:00Z",
                        "priority": "low",
                        "body": {"episodeIds": [1, 2, 3]},
                    },
                    {"name": "RefreshMonitoredDownloads", "status": "completed"},
                ],
                now,
            ),
            "started EpisodeSearch 14m low (Show 1 episode(s)); "
            "queued EpisodeSearch 10m low (3 episode(s))",
        )
        self.assertEqual(ws.summarize_arr_commands([], now), "none queued or started")
        self.assertEqual(
            ws.video_episode_key(
                "Silo.S03E01.Who.Are.You.1080p.WEBRip.10Bit.DDP5.1.x265-NeoNoir.mkv"
            ),
            "S03E01",
        )
        self.assertEqual(
            ws.video_episode_key("Silo.S03E04.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv"),
            "S03E04",
        )
        self.assertEqual(
            ws.video_episode_key("Wake Up Dead Man A Knives Out Mystery 2025.mkv"),
            "",
        )
        dest = Path(os.environ.get("TEST_TMP") or "/tmp") / f"lib-has-{os.getpid()}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            (dest / "Silo.S03E04.mkv").write_bytes(b"ok")
            self.assertTrue(ws.library_has_this_release(str(dest), "Silo.S03E04.mkv"))
            self.assertFalse(ws.library_has_this_release(str(dest), "Silo.S03E01.mkv"))
            self.assertTrue(
                ws.library_has_this_release(str(dest), "Wake Up Dead Man.mkv")
            )
        finally:
            for leftover in dest.glob("*"):
                leftover.unlink()
            dest.rmdir()
        self.assertEqual(
            ws.missing_episode_label(
                {"seasonNumber": 1, "episodeNumber": 4, "series": {"title": "Show"}}
            ),
            "Show S01E04",
        )
        self.assertEqual(
            ws.import_rejection_text(
                {"rejections": [{"reason": "Not a wanted quality"}]}
            ),
            "Not a wanted quality",
        )

    def test_queue_logs_finished_drop_even_without_arr_warning(self):
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.assertTrue(
            ws.queue_needs_import_log(
                {
                    "title": "Silent",
                    "status": "downloading",
                    "trackedDownloadStatus": "ok",
                    "trackedDownloadState": "downloading",
                    "outputPath": "/media/dlna/downloads/complete/Silent",
                }
            )
        )
        self.assertFalse(
            ws.queue_needs_import_log(
                {
                    "title": "Active",
                    "status": "downloading",
                    "trackedDownloadStatus": "ok",
                    "trackedDownloadState": "downloading",
                    "outputPath": "/media/dlna/downloads/incomplete/Silent",
                }
            )
        )
        self.assertTrue(
            ws.queue_needs_import_log(
                {
                    "title": "Warned",
                    "trackedDownloadStatus": "warning",
                    "trackedDownloadState": "importPending",
                    "outputPath": "",
                }
            )
        )
        os.environ.pop("MEDIA_ROOT", None)

    def test_qbit_payload_gone_treats_empty_dir_as_moved(self):
        empty = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-gone-{os.getpid()}"
        empty.mkdir(parents=True, exist_ok=True)
        try:
            self.assertTrue(ws.qbit_payload_gone({"content_path": str(empty)}))
            (empty / "notes.nfo").write_text("nfo")
            (empty / "English.srt").write_text("1")
            self.assertTrue(ws.qbit_payload_gone({"content_path": str(empty)}))
            (empty / "file.mkv").write_text("x")
            self.assertFalse(ws.qbit_payload_gone({"content_path": str(empty)}))
            self.assertTrue(
                ws.qbit_payload_gone({"content_path": str(empty / "missing.mkv")})
            )
            self.assertTrue(ws.qbit_payload_gone({"content_path": str(empty / "notes.nfo")}))
        finally:
            for child in empty.iterdir():
                child.unlink()
            empty.rmdir()

    def test_rebuild_hd_items_disallows_remux_and_4k(self):
        catalog = ws.quality_catalog(any_quality_bundle()["profile"])
        items = ws.rebuild_hd_items(catalog)
        allowed, blocked = allowed_quality_names({"items": items})
        self.assertIn("Bluray-1080p", allowed)
        self.assertIn("WEBDL-1080p", allowed)
        self.assertIn("WEBRip-1080p", allowed)
        self.assertIn("Remux-1080p", blocked)
        self.assertIn("Remux-2160p", blocked)
        self.assertIn("WEBDL-2160p", blocked)
        self.assertIn("CAM", blocked)

    def test_max_items_allow_remux_and_reject_cam(self):
        catalog = ws.quality_catalog(any_quality_bundle()["profile"])
        items = ws.rebuild_profile_items(catalog, ws.MAX_GROUPS)
        allowed, blocked = allowed_quality_names({"items": items})
        self.assertIn("Remux-1080p", allowed)
        self.assertIn("Remux-2160p", allowed)
        self.assertIn("Bluray-1080p", allowed)
        self.assertIn("CAM", blocked)
        self.assertIn("BR-DISK", blocked)

    def test_anything_items_allow_cam(self):
        catalog = ws.quality_catalog(any_quality_bundle()["profile"])
        items = ws.rebuild_profile_items(catalog, (), allow_unlisted=True)
        allowed, blocked = allowed_quality_names({"items": items})
        self.assertIn("CAM", allowed)
        self.assertIn("WEBDL-480p", allowed)
        self.assertIn("Remux-2160p", allowed)
        self.assertIn("BR-DISK", blocked)

    def test_rebuild_assigns_nonzero_group_ids(self):
        catalog = ws.quality_catalog(any_quality_bundle()["profile"])
        items = ws.rebuild_profile_items(catalog, ws.DEFAULT_GROUPS)
        groups = [item for item in items if item.get("quality") is None]
        self.assertTrue(groups)
        ids = [int(item["id"]) for item in groups]
        self.assertTrue(all(gid >= 1000 for gid in ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_cutoff_from_items_uses_group_id_not_child_quality(self):
        items = [
            {
                "id": 1001,
                "name": "WEB 1080p",
                "allowed": True,
                "items": [
                    {"quality": {"id": 11, "name": "WEBDL-1080p"}, "items": [], "allowed": True},
                    {"quality": {"id": 12, "name": "WEBRip-1080p"}, "items": [], "allowed": True},
                ],
            },
            {"quality": {"id": 13, "name": "Bluray-1080p"}, "items": [], "allowed": True},
        ]
        self.assertEqual(
            ws.cutoff_id_from_items(items, ("WEBDL-1080p", "Bluray-1080p")),
            1001,
        )
        self.assertEqual(ws.cutoff_id_from_items(items, ("Bluray-1080p",)), 13)

    def test_language_profile_id_skips_http(self):
        self.assertIsNone(ws.language_profile_id("http://127.0.0.1:8989/api/v3", "k"))

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


class LogEmit(unittest.TestCase):
    def setUp(self):
        emitmod._last_emitted.clear()

    def captured(self, name: str, lines: list[str]) -> tuple[str, str]:
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr

        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            for line in lines:
                emitmod.emit(name, line)
        return out.getvalue(), err.getvalue()

    def test_keeps_structured_arr_warn_and_error(self):
        out, err = self.captured(
            "Radarr",
            ["WebUI started", "|Error| disk full", "|Warn| slow disk"],
        )
        self.assertIn("[Radarr] WebUI started", out)
        self.assertIn("[Radarr] |Error| disk full", err)
        self.assertIn("ERROR", err)
        self.assertIn("[Radarr] |Warn| slow disk", err)
        self.assertIn("WARNING", err)

    def test_drops_nzbdrone_stack_frames_and_rewrites_exception_type(self):
        out, err = self.captured(
            "Sonarr",
            [
                "   at NzbDrone.Core.Indexers.HttpIndexerBase`1.FetchPage(IndexerRequest request)",
                "   at System.Runtime.CompilerServices.TaskAwaiter.HandleNonSuccessAndDebuggerNotification",
                "NzbDrone.Common.Http.TooManyRequestsException: HTTP request failed: [429:TooManyRequests]",
            ],
        )
        combined = out + err
        self.assertNotIn("at NzbDrone", combined)
        self.assertNotIn("at System.", combined)
        self.assertNotIn("at Arr.", combined)
        self.assertIn("Arr.Common.Http.TooManyRequestsException", err)
        self.assertIn("WARNING", err)
        self.assertNotIn("NzbDrone", combined)

    def test_drops_seerr_debug_plex_scan_and_ansi(self):
        debug = (
            "2026-08-29T10:00:49.211Z [\x1b[34mdebug\x1b[39m][Plex Scan]: "
            "Title already exists and no changes detected for Rust"
        )
        error = (
            "2026-08-29T10:01:11.862Z [\x1b[31merror\x1b[39m][Plex Scan]: "
            "Failed to process Plex media"
        )
        out, err = self.captured("Seerr", [debug, error])
        combined = out + err
        self.assertNotIn("already exists", combined)
        self.assertNotIn("\x1b[", combined)
        self.assertIn("[error][Plex Scan]: Failed to process Plex media", err)
        self.assertIn("ERROR", err)

    def test_redacts_apikey_and_jwt_and_drops_json_dump(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJwb21wZXktdGVzdCJ9.signaturepart"
        )
        out, err = self.captured(
            "Prowlarr",
            [
                "[Warn] HttpClient: HTTP Error - Res: [GET] http://127.0.0.1:9698/4/api?apikey=supersecretkey&t=tvsearch",
                '    "errorMessage": "Unable to connect to indexer"',
                '    "severity": "error"',
                "<error code=\"429\" description=\"Indexer is disabled\" />",
                f"[error]: Failed to enrich TMDB show token: {jwt}",
            ],
        )
        combined = out + err
        self.assertNotIn("supersecretkey", combined)
        self.assertIn("apikey=(redacted)", err)
        self.assertNotIn("errorMessage", combined)
        self.assertNotIn("severity", combined)
        self.assertNotIn("<error code", combined)
        self.assertNotIn(jwt, combined)
        self.assertIn("token:(redacted)", err)

    def test_drops_consecutive_duplicate_lines(self):
        _, err = self.captured(
            "Prowlarr",
            [
                "[Warn] RadarrV3Proxy: No Results in configured categories",
                "[Warn] RadarrV3Proxy: No Results in configured categories",
            ],
        )
        self.assertEqual(err.count("No Results in configured categories"), 1)


class VpnStats(unittest.TestCase):
    def test_parse_net_dev_rx_tx(self):
        text = (
            "Inter-|   Receive                                                |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast|"
            "bytes    packets errs drop fifo frame compressed\n"
            "    lo: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n"
            "  wg0: 12345678 10 0 0 0 0 0 0 98765 4 0 0 0 0 0 0\n"
        )
        self.assertEqual(vpnstats.parse_net_dev(text, "wg0"), (12345678, 98765))
        self.assertIsNone(vpnstats.parse_net_dev(text, "eth0"))

    def test_write_once_merges_vpn_without_clobbering_boot(self):
        tmp = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-vpn-{os.getpid()}"
        tmp.mkdir(parents=True, exist_ok=True)
        status = tmp / "status.json"
        netdev = tmp / "net-dev"
        status.write_text(
            json.dumps(
                {
                    "step": "ready",
                    "label": "Ready",
                    "percent": 100,
                    "need_proton": False,
                    "search": True,
                    "steps": [{"id": "ready", "label": "Ready", "state": "done"}],
                }
            )
        )
        netdev.write_text(
            "wg0: 5000000 1 0 0 0 0 0 0 400000 1 0 0 0 0 0 0\n"
        )
        old = {
            "POMPEY_STATUS": os.environ.get("POMPEY_STATUS"),
            "POMPEY_NET_DEV": os.environ.get("POMPEY_NET_DEV"),
            "WG_IFACE": os.environ.get("WG_IFACE"),
        }
        try:
            os.environ["POMPEY_STATUS"] = str(status)
            os.environ["POMPEY_NET_DEV"] = str(netdev)
            os.environ["WG_IFACE"] = "wg0"
            self.assertEqual(vpnstats.write_once(), 0)
            data = json.loads(status.read_text())
            self.assertEqual(data["step"], "ready")
            self.assertTrue(data["search"])
            self.assertEqual(data["vpn"]["iface"], "wg0")
            self.assertTrue(data["vpn"]["up"])
            self.assertEqual(data["vpn"]["rx_bytes"], 5000000)
            self.assertEqual(data["vpn"]["tx_bytes"], 400000)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


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
                "AFTER_DOWNLOAD": "stop_sharing",
                "PREFERRED_LANGUAGE": "english",
                "ANIME_AUDIO": "dual_audio",
                "SUBTITLES": "english",
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
        for client in self.state.download_clients:
            self.assertTrue(client.get("removeCompletedDownloads"))
            self.assertTrue(client.get("removeFailedDownloads"))
            fields = {f["name"]: f.get("value") for f in client.get("fields") or []}
            self.assertIn(fields.get("movieCategory") or fields.get("tvCategory"), {"radarr", "sonarr"})
        self.assertEqual(self.state.qbit_categories.get("radarr"), "/media/downloads/complete")
        self.assertEqual(self.state.qbit_categories.get("sonarr"), "/media/downloads/complete")
        self.assertTrue(self.state.radarr_media.get("skipFreeSpaceCheckWhenImporting"))
        self.assertTrue(self.state.sonarr_media.get("enableCompletedDownloadHandling"))
        self.assertEqual(self.state.radarr_media.get("minimumFreeSpaceWhenImporting"), 100)
        self.assertFalse(self.state.radarr_media.get("copyUsingHardlinks"))
        self.assertFalse(self.state.sonarr_media.get("copyUsingHardlinks"))
        self.assertTrue(self.state.radarr_media.get("importExtraFiles"))
        self.assertTrue(self.state.sonarr_media.get("importExtraFiles"))
        self.assertEqual(self.state.radarr_media.get("extraFileExtensions"), "srt")
        self.assertEqual(self.state.sonarr_media.get("extraFileExtensions"), "srt")
        self.assertFalse(self.state.radarr_dl_config.get("autoRedownloadFailed"))
        self.assertTrue(self.state.radarr_dl_config.get("enableCompletedDownloadHandling"))
        cmd_names = [c.get("name") for c in self.state.arr_commands]
        self.assertNotIn("RefreshMonitoredDownloads", cmd_names)
        self.assertEqual(
            self.state.seerr_jobs,
            ["plex-recently-added-scan", "radarr-scan", "sonarr-scan"],
        )
        self.assertEqual(self.state.qbit_removed, [])
        self.assertEqual({a["name"] for a in self.state.apps}, {"Sonarr", "Radarr"})
        for app in self.state.apps:
            fields = {f["name"]: f.get("value") for f in app.get("fields") or []}
            self.assertEqual(fields.get("prowlarrUrl"), "http://127.0.0.1:9698")
            if app["name"] == "Radarr":
                self.assertEqual(fields.get("syncCategories"), ws.RADARR_SYNC_CATS)
            else:
                self.assertEqual(fields.get("syncCategories"), ws.SONARR_SYNC_CATS)
        lang_gets = [
            call
            for call in self.state.calls
            if call[1] == "GET" and str(call[2]).endswith("/languageprofile")
        ]
        self.assertEqual(lang_gets, [])
        self.assertEqual(len(self.state.indexers), 1)
        self.assertEqual(
            [c.get("name") for c in self.state.commands],
            ["ApplicationIndexerSync"],
        )
        source_fields = {f["name"]: f.get("value") for f in self.state.indexers[0]["fields"]}
        self.assertEqual(source_fields["apiKey"], "test-source-key")
        self.assertEqual(self.state.plex_auth, {"authToken": "test-plex-token"})
        self.assertEqual(self.state.seerr_radarr[0]["hostname"], "127.0.0.1")
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Default")
        self.assertEqual(self.state.seerr_sonarr[0]["activeProfileName"], "Default")
        self.assertEqual(self.state.seerr_sonarr[0]["activeDirectory"], "/media/TV/Not Kid Friendly")
        self.assertTrue(self.state.seerr_sonarr[0].get("enableSeasonFolders"))
        self.assertTrue((self.ready / "seerr-arr").exists())
        self.assertTrue(self.state.initialized)
        self.assertFalse(self.nginx.exists(), "Ingress must stay the Pompey UI, not a Seerr proxy")
        live = json.loads((self.ready / "status.json").read_text())
        self.assertTrue(live["search"])
        self.assertEqual(live["search_port"], 5055)
        self.assertEqual(live["sources_port"], 9696)
        self.assertIsNone(self.state.local_auth)
        local_posts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "POST" and call[2] == "/api/v1/auth/local"
        ]
        self.assertEqual(local_posts, [])

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

    def test_turns_on_search_flags_when_list_omits_them(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.indexers = [
            {"id": 1, "name": "LimeTorrents", "enable": True},
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        item = self.state.indexers[0]
        self.assertTrue(item["enableRss"])
        self.assertTrue(item["enableAutomaticSearch"])
        self.assertTrue(item["enableInteractiveSearch"])
        puts = [
            call
            for call in self.state.calls
            if call[0] == "prowlarr" and call[1] == "PUT" and call[2] == "/api/v1/indexer/1"
        ]
        self.assertTrue(puts)

    def test_warns_when_radarr_is_missing_prowlarr_sources(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        flags = {
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": True,
        }
        self.state.indexers = [
            {"id": 1, "name": "Nyaa.si", "enable": True, **flags},
            {"id": 2, "name": "LimeTorrents", "enable": True, **flags},
        ]
        self.state.radarr_indexers = [{"id": 1, "name": "Nyaa.si", "enable": True}]
        self.state.sonarr_indexers = [
            {"id": 1, "name": "Nyaa.si", "enable": True},
            {"id": 2, "name": "LimeTorrents", "enable": True},
        ]
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.main()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("Radarr indexers: 1 (Prowlarr enabled 2)", out)
        self.assertIn("missing Prowlarr source(s): LimeTorrents", out)
        self.assertIn("Sonarr indexers: 2 (Prowlarr enabled 2)", out)

    def test_applies_three_quality_tiers(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        extra = json.loads(json.dumps(any_quality_bundle(2, "HD-720p")["profile"]))
        ultra = json.loads(json.dumps(any_quality_bundle(3, "Ultra-HD")["profile"]))
        self.state.radarr_profiles.extend([extra, ultra])
        self.state.radarr_profiles[0]["name"] = "HD"
        rc = ws.main()
        self.assertEqual(rc, 0)
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertEqual(names, {"Max", "Default", "Anything"})
        default = profile_named(self.state.radarr_profiles, "Default")
        maximum = profile_named(self.state.radarr_profiles, "Max")
        anything = profile_named(self.state.radarr_profiles, "Anything")
        allowed, blocked = allowed_quality_names(default)
        self.assertIn("Bluray-1080p", allowed)
        self.assertIn("WEBDL-1080p", allowed)
        self.assertIn("Remux-1080p", blocked)
        self.assertIn("Remux-2160p", blocked)
        self.assertIn("CAM", blocked)
        max_allowed, max_blocked = allowed_quality_names(maximum)
        self.assertIn("Remux-1080p", max_allowed)
        self.assertIn("Remux-2160p", max_allowed)
        self.assertIn("CAM", max_blocked)
        any_allowed, _any_blocked = allowed_quality_names(anything)
        self.assertIn("CAM", any_allowed)
        self.assertFalse(anything.get("upgradeAllowed"))
        bluray_id = next(
            item["quality"]["id"]
            for item in any_quality_bundle()["profile"]["items"]
            if item["quality"]["name"] == "Bluray-1080p"
        )
        remux_4k_id = next(
            item["quality"]["id"]
            for item in any_quality_bundle()["profile"]["items"]
            if item["quality"]["name"] == "Remux-2160p"
        )
        self.assertEqual(default["cutoff"], bluray_id)
        self.assertEqual(maximum["cutoff"], remux_4k_id)
        cf_names = {item["name"] for item in self.state.radarr_formats}
        self.assertIn("Pompey Prefer x265", cf_names)
        self.assertIn("Pompey Prefer Remux", cf_names)
        self.assertIn("Pompey Dual Audio", cf_names)
        self.assertIn("Pompey English subs", cf_names)
        scores = {item["name"]: item["score"] for item in default.get("formatItems") or []}
        self.assertEqual(scores["Pompey Reject Remux/DISK"], -10000)
        self.assertEqual(scores["Pompey Prefer x265"], 80)
        self.assertEqual(scores["Pompey Dual Audio"], 200)
        self.assertEqual(scores["Pompey English subs"], 50)
        max_scores = {item["name"]: item["score"] for item in maximum.get("formatItems") or []}
        self.assertEqual(max_scores["Pompey Prefer Remux"], 200)
        self.assertEqual(max_scores["Pompey Prefer lossless audio"], 150)
        self.assertEqual(max_scores.get("Pompey Reject Remux/DISK", 0), 0)
        cf_item_names = {item["name"] for item in default.get("formatItems") or []}
        self.assertEqual(cf_item_names, cf_names)
        self.assertGreaterEqual(default.get("minUpgradeFormatScore") or 0, 1)
        web_group = next(
            item
            for item in default["items"]
            if item.get("name") == "WEB 1080p" or (
                isinstance(item.get("quality"), dict) and item["quality"].get("name") == "WEB 1080p"
            )
        )
        if web_group.get("quality") is None:
            self.assertGreater(int(web_group["id"]), 0)
        web = next(item for item in self.state.radarr_defs if item["quality"]["name"] == "WEBDL-1080p")
        self.assertEqual(web["minSize"], 12.5)
        self.assertEqual(web["preferredSize"], 33.0)
        self.assertEqual(web["maxSize"], 53.0)
        remux = next(item for item in self.state.radarr_defs if item["quality"]["name"] == "Remux-1080p")
        self.assertEqual(remux["maxSize"], 320.0)
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Default")
        self.assertEqual(self.state.seerr_sonarr[0]["activeProfileName"], "Default")
        sonarr_names = {item.get("name") for item in self.state.sonarr_profiles}
        self.assertEqual(sonarr_names, {"Max", "Default", "Anything"})
        self.assertEqual(self.state.seerr_main["defaultPermissions"], ws.SEERR_HOUSEHOLD_PERMS)
        self.assertTrue(ws.SEERR_HOUSEHOLD_PERMS & ws.SEERR_REQUEST_ADVANCED)

    def test_quality_post_empty_body_still_wires(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.quality_post_empty_body = True
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertEqual(names, {"Max", "Default", "Anything"})
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Default")

    def test_quality_put_400_does_not_block_wire(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.fail_quality_profiles = True
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertTrue((self.ready / "seerr-arr").exists())
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertEqual(names, {"Any"})
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Any")

    def test_keeps_stock_profiles_until_three_exist(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        extra = json.loads(json.dumps(any_quality_bundle(2, "HD-720p")["profile"]))
        self.state.radarr_profiles.append(extra)
        self.state.reject_profile_names = {"Max"}
        rc = ws.main()
        self.assertEqual(rc, 0)
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertIn("Default", names)
        self.assertIn("Anything", names)
        self.assertIn("HD-720p", names)
        self.assertNotIn("Max", names)

    def test_grants_advanced_requests_to_existing_seerr_user(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_users = [{"id": 2, "email": "house@local", "permissions": 32 + 128}]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue(self.state.seerr_users[0]["permissions"] & ws.SEERR_REQUEST_ADVANCED)

    def test_original_anime_audio_skips_dual_audio_score(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["ANIME_AUDIO"] = "original"
        os.environ["PREFERRED_LANGUAGE"] = "original"
        os.environ["SUBTITLES"] = "none"
        rc = ws.main()
        self.assertEqual(rc, 0)
        default = profile_named(self.state.radarr_profiles, "Default")
        scores = {item["name"]: item["score"] for item in default.get("formatItems") or []}
        self.assertEqual(scores.get("Pompey Dual Audio", 0), 0)
        self.assertEqual(scores.get("Pompey English dub", 0), 0)
        self.assertEqual(scores.get("Pompey English subs", 0), 0)

    def test_share_to_ratio_leaves_torrent_until_ratio(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["AFTER_DOWNLOAD"] = "share_to_ratio"
        rc = ws.main()
        self.assertEqual(rc, 0)
        for client in self.state.download_clients:
            self.assertFalse(client.get("removeCompletedDownloads"))
            self.assertTrue(client.get("removeFailedDownloads"))

    def test_forgets_qbit_torrent_after_files_were_moved(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        still_downloading = {
            "hash": "11" * 20,
            "name": "in-progress",
            "state": "downloading",
            "progress": 0.4,
            "amount_left": 900,
            "content_path": str(self.tmp / "incomplete-file.mkv"),
        }
        (self.tmp / "incomplete-file.mkv").write_text("partial")
        moved = {
            "hash": "22" * 20,
            "name": "already-in-library",
            "state": "missingFiles",
            "progress": 1,
            "amount_left": 0,
            "content_path": str(self.tmp / "complete-gone.mkv"),
        }
        seeding_present = {
            "hash": "33" * 20,
            "name": "still-on-disk",
            "state": "uploading",
            "progress": 1,
            "amount_left": 0,
            "content_path": str(self.tmp / "complete-file.mkv"),
        }
        (self.tmp / "complete-file.mkv").write_text("ok")
        self.state.qbit_torrents = [still_downloading, moved, seeding_present]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.state.qbit_removed), 1)
        self.assertEqual(self.state.qbit_removed[0]["deleteFiles"], "false")
        self.assertEqual(self.state.qbit_removed[0]["hashes"], "22" * 20)
        remaining = {item["hash"] for item in self.state.qbit_torrents}
        self.assertEqual(remaining, {"11" * 20, "33" * 20})
        self.assertTrue((self.tmp / "complete-file.mkv").is_file())
        self.assertTrue((self.tmp / "incomplete-file.mkv").is_file())
        self.assertEqual(self.state.qbit_stopped, [])

    def test_housekeep_stops_seeding_torrent_in_complete(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = str(self.tmp)
        complete = self.tmp / "downloads" / "complete"
        complete.mkdir(parents=True, exist_ok=True)
        payload = complete / "new-grab.mkv"
        payload.write_bytes(b"x" * 40)
        digest = "55" * 20
        self.state.qbit_torrents = [
            {
                "hash": digest,
                "name": "new-grab",
                "state": "uploading",
                "progress": 1,
                "amount_left": 0,
                "content_path": str(payload),
            }
        ]
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(self.state.qbit_removed, [])
        self.assertEqual(self.state.qbit_stopped[0]["hashes"], digest)
        self.assertEqual(self.state.qbit_stopped[0]["action"], "stop")
        self.assertTrue(payload.is_file())
        remaining = [item for item in self.state.qbit_torrents if item.get("hash") == digest]
        self.assertEqual(remaining[0]["state"], "stoppedUP")

    def test_housekeep_renames_matched_drop_and_logs_quality_reject(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = str(self.tmp)
        complete = self.tmp / "downloads" / "complete"
        complete.mkdir(parents=True, exist_ok=True)
        wanted = complete / "matched.mkv"
        blocked = complete / "remux.mkv"
        wanted.write_bytes(b"ok")
        blocked.write_bytes(b"no")
        quality = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}}
        dest = str(self.tmp / "Movies" / "Not Kid Friendly" / "Matched (2024)")
        unknown = complete / "random-file.mkv"
        unknown.write_bytes(b"z")
        self.state.manual_import = [
            {
                "path": str(wanted),
                "movieId": 1,
                "movie": {
                    "id": 1,
                    "title": "Matched",
                    "path": dest,
                    "hasFile": False,
                },
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            },
            {
                "path": str(blocked),
                "movieId": 1,
                "quality": quality,
                "rejections": [{"reason": "Not a wanted quality for Default"}],
            },
            {
                "path": str(unknown),
                "quality": quality,
                "rejections": [],
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(len(manuals), 1)
        self.assertEqual(manuals[0].get("importMode"), "Move")
        paths = [row.get("path") for row in manuals[0].get("files") or []]
        self.assertEqual(paths, [str(wanted)])
        out = buf.getvalue()
        self.assertIn("will not import remux.mkv: Not a wanted quality for Default", out)
        self.assertIn(f"importing matched.mkv into {dest} (Arr record from Seerr, not the filename)", out)
        self.assertIn(
            "no library match for random-file.mkv (not guessing Kid vs Not Kid from the name)",
            out,
        )

    def test_housekeep_does_not_delete_torrent_data(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.qbit_torrents = [
            {
                "hash": "44" * 20,
                "name": "hand-moved",
                "state": "missingFiles",
                "progress": 1,
                "content_path": "/no/such/file.mkv",
            }
        ]
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(self.state.qbit_removed[0]["deleteFiles"], "false")
        self.assertNotIn("true", self.state.qbit_removed[0]["deleteFiles"].lower())
        self.assertFalse(any(item.get("hash") == "44" * 20 for item in self.state.qbit_torrents))

    def test_housekeep_forgets_torrent_when_only_extras_remain(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "forget-extras"
        leftover = root / "downloads" / "complete" / "Marty leftover"
        leftover.mkdir(parents=True)
        (leftover / "English.srt").write_bytes(b"sub")
        os.environ["MEDIA_ROOT"] = str(root)
        digest = "66" * 20
        self.state.qbit_torrents = [
            {
                "hash": digest,
                "name": "Marty leftover",
                "state": "stoppedUP",
                "progress": 1,
                "amount_left": 0,
                "content_path": str(leftover),
            }
        ]
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(self.state.qbit_removed[0]["hashes"], digest)
        self.assertEqual(self.state.qbit_removed[0]["deleteFiles"], "false")
        self.assertFalse(leftover.exists())
        self.assertFalse(any(item.get("hash") == digest for item in self.state.qbit_torrents))

    def test_housekeep_scans_legacy_category_folders_per_kind(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = str(self.tmp)
        complete = self.tmp / "downloads" / "complete"
        radarr_dir = complete / "radarr"
        sonarr_dir = complete / "sonarr"
        radarr_dir.mkdir(parents=True)
        sonarr_dir.mkdir(parents=True)
        (radarr_dir / "stuck-title.mkv").write_bytes(b"x" * 50)
        (sonarr_dir / "stuck-episode.mkv").write_bytes(b"x" * 50)
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        movie_paths = {
            item.get("path")
            for item in self.state.arr_commands
            if item.get("name") == "DownloadedMoviesScan"
        }
        episode_paths = {
            item.get("path")
            for item in self.state.arr_commands
            if item.get("name") == "DownloadedEpisodesScan"
        }
        self.assertEqual(movie_paths, {str(complete), str(radarr_dir)})
        self.assertEqual(episode_paths, {str(complete), str(sonarr_dir)})
        out = buf.getvalue()
        self.assertIn("radarr/stuck-title.mkv (50 bytes)", out)
        self.assertIn("sonarr/stuck-episode.mkv (50 bytes)", out)

    def test_housekeep_skips_manual_import_when_library_already_has_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "skip-hasfile"
        release = root / "downloads" / "complete" / "www.UIndex.org - Already"
        release.mkdir(parents=True)
        leftover = release / "already-imported.mkv"
        leftover.write_bytes(b"copy")
        (release / "English.srt").write_bytes(b"sub")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "Movies" / "Not Kid Friendly" / "Already (2024)"
        dest.mkdir(parents=True)
        (dest / "Already (2024).mkv").write_bytes(b"library")
        quality = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}}
        for movie in self.state.movies:
            if movie.get("id") == 2:
                movie["hasFile"] = True
                movie["path"] = str(dest)
        self.state.manual_import = [
            {
                "path": str(leftover),
                "movieId": 2,
                "movie": {
                    "id": 2,
                    "title": "Already",
                    "path": str(dest),
                },
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(manuals, [])
        self.assertIn(
            f"already has a library file for already-imported.mkv -> {dest} "
            "(not re-importing leftover complete/ files)",
            buf.getvalue(),
        )
        self.assertFalse(release.exists())
        self.assertTrue((dest / "Already (2024).mkv").is_file())

    def test_housekeep_skips_sonarr_import_when_episode_already_has_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "skip-sonarr-hasfile"
        release = root / "downloads" / "complete" / "Show.S01E01"
        release.mkdir(parents=True)
        leftover = release / "Show.S01E01.mkv"
        leftover.write_bytes(b"copy")
        (release / "English.srt").write_bytes(b"sub")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Show (2024)"
        dest.mkdir(parents=True)
        (dest / "Show.S01E01.mkv").write_bytes(b"library")
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.series = [
            {
                "id": 10,
                "title": "Show",
                "monitored": True,
                "path": str(dest),
                "statistics": {"episodeFileCount": 1, "episodeCount": 1},
            }
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": True, "title": "Pilot"},
        ]
        self.state.sonarr_manual_import = [
            {
                "path": str(leftover),
                "seriesId": 10,
                "episodeIds": [11],
                "series": {"id": 10, "title": "Show", "path": str(dest)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(manuals, [])
        self.assertIn(
            f"already has a library file for Show.S01E01.mkv -> {dest} "
            "(not re-importing leftover complete/ files)",
            buf.getvalue(),
        )
        self.assertFalse(release.exists())
        self.assertTrue((dest / "Show.S01E01.mkv").is_file())

    def test_housekeep_removes_loose_complete_file_when_library_has_episode(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "loose-hasfile"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S03E01.Who.Are.You.1080p.WEBRip.mkv"
        leftover.write_bytes(b"copy")
        movie = complete / "Wake Up Dead Man 2025.mkv"
        movie.write_bytes(b"copy")
        os.environ["MEDIA_ROOT"] = str(root)
        tv = root / "TV" / "Not Kid Friendly" / "Silo"
        tv.mkdir(parents=True)
        (tv / "Silo.S03E01.mkv").write_bytes(b"library")
        (tv / "Silo.S03E04.mkv").write_bytes(b"other")
        films = root / "Movies" / "Not Kid Friendly" / "Wake Up Dead Man (2025)"
        films.mkdir(parents=True)
        (films / "Wake Up Dead Man (2025).mkv").write_bytes(b"library")
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        for movie_row in self.state.movies:
            if movie_row.get("id") == 2:
                movie_row["hasFile"] = True
                movie_row["path"] = str(films)
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(tv),
                "statistics": {"episodeFileCount": 2, "episodeCount": 2},
            }
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": True, "title": "Who Are You?"},
        ]
        self.state.manual_import = [
            {
                "path": str(movie),
                "movieId": 2,
                "movie": {"id": 2, "title": "Wake Up Dead Man", "path": str(films)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        self.state.sonarr_manual_import = [
            {
                "path": str(leftover),
                "seriesId": 10,
                "episodeIds": [11],
                "series": {"id": 10, "title": "Silo", "path": str(tv)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(manuals, [])
        self.assertFalse(leftover.exists())
        self.assertFalse(movie.exists())
        self.assertTrue((tv / "Silo.S03E01.mkv").is_file())
        self.assertTrue((films / "Wake Up Dead Man (2025).mkv").is_file())
        self.assertIn("removed leftover complete/ file", buf.getvalue())

    def test_housekeep_imports_loose_episode_when_library_has_other_episodes(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "loose-missing-ep"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S03E01.Who.Are.You.1080p.WEBRip.mkv"
        leftover.write_bytes(b"copy")
        os.environ["MEDIA_ROOT"] = str(root)
        tv = root / "TV" / "Not Kid Friendly" / "Silo"
        tv.mkdir(parents=True)
        (tv / "Silo.S03E04.mkv").write_bytes(b"other")
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(tv),
                "statistics": {"episodeFileCount": 1, "episodeCount": 2},
            }
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": True, "title": "Who Are You?"},
        ]
        self.state.sonarr_manual_import = [
            {
                "path": str(leftover),
                "seriesId": 10,
                "episodeIds": [11],
                "series": {"id": 10, "title": "Silo", "path": str(tv)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(len(manuals), 1)
        self.assertEqual(manuals[0].get("importMode"), "Move")
        self.assertEqual(
            [row.get("path") for row in manuals[0].get("files") or []],
            [str(leftover)],
        )
        self.assertTrue(leftover.exists())
        self.assertIn("does not have this video; importing leftover", buf.getvalue())

    def test_housekeep_renames_matched_sonarr_drop(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "sonarr-import"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        wanted = complete / "Show.S01E02.mkv"
        wanted.write_bytes(b"ok")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = str(root / "TV" / "Not Kid Friendly" / "Show (2024)")
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.sonarr_manual_import = [
            {
                "path": str(wanted),
                "seriesId": 10,
                "episodeIds": [12],
                "series": {"id": 10, "title": "Show", "path": dest, "hasFile": False},
                "episodes": [{"id": 12, "hasFile": False}],
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(len(manuals), 1)
        self.assertEqual(manuals[0].get("importMode"), "Move")
        self.assertEqual(
            [row.get("path") for row in manuals[0].get("files") or []],
            [str(wanted)],
        )
        self.assertIn(
            f"importing Show.S01E02.mkv into {dest} (Arr record from Seerr, not the filename)",
            buf.getvalue(),
        )

    def test_housekeep_does_not_log_unknown_movie_for_an_episode_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "cross-kind-movie"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        episode = complete / "Show.S01E01.mkv"
        episode.write_bytes(b"x")
        os.environ["MEDIA_ROOT"] = str(root)
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.manual_import = [
            {
                "path": str(episode),
                "quality": quality,
                "rejections": [{"reason": "Unknown Movie"}],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertNotIn("Unknown Movie", buf.getvalue())

    def test_housekeep_logs_extras_not_videos_in_complete(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "extras-only"
        folder = root / "downloads" / "complete" / "Marty leftover"
        folder.mkdir(parents=True)
        (folder / "English.srt").write_bytes(b"sub")
        (folder / "release.nfo").write_bytes(b"nfo")
        os.environ["MEDIA_ROOT"] = str(root)
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("removed 1 leftover complete/ folder(s)", out)
        self.assertFalse(folder.exists())
        self.assertNotIn("still in complete/:", out)
        self.assertNotIn("English.srt", out)

    def test_housekeep_does_not_log_unknown_series_for_a_movie_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "cross-kind"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        movie = complete / "The.Shadows.Edge.mp4"
        movie.write_bytes(b"x")
        os.environ["MEDIA_ROOT"] = str(root)
        quality = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}}
        self.state.manual_import = [
            {
                "path": str(movie),
                "quality": quality,
                "rejections": [{"reason": "Unknown Series"}],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertNotIn("Unknown Series", buf.getvalue())

    def test_housekeep_asks_seerr_to_notice_library_files(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.state.seerr_jobs,
            ["plex-recently-added-scan", "radarr-scan", "sonarr-scan"],
        )

    def test_updates_existing_download_client_remove_flag(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["AFTER_DOWNLOAD"] = "stop_sharing"
        self.state.radarr_clients = [
            {
                "id": 7,
                "name": "qBittorrent",
                "implementation": "QBittorrent",
                "removeCompletedDownloads": False,
                "removeFailedDownloads": False,
            }
        ]
        self.state.sonarr_clients = [
            {
                "id": 8,
                "name": "qBittorrent",
                "implementation": "QBittorrent",
                "removeCompletedDownloads": False,
                "removeFailedDownloads": False,
            }
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue(self.state.radarr_clients[0]["removeCompletedDownloads"])
        self.assertTrue(self.state.radarr_clients[0]["removeFailedDownloads"])
        self.assertEqual(self.state.radarr_clients[0]["id"], 7)
        radarr_fields = {
            f["name"]: f.get("value") for f in self.state.radarr_clients[0].get("fields") or []
        }
        self.assertEqual(radarr_fields.get("movieCategory"), "radarr")
        self.assertTrue(self.state.sonarr_clients[0]["removeCompletedDownloads"])
        self.assertTrue(self.state.sonarr_clients[0]["removeFailedDownloads"])
        self.assertEqual(self.state.sonarr_clients[0]["id"], 8)
        sonarr_fields = {
            f["name"]: f.get("value") for f in self.state.sonarr_clients[0].get("fields") or []
        }
        self.assertEqual(sonarr_fields.get("tvCategory"), "sonarr")

    def test_media_management_skips_nas_free_space_check(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        rc = ws.main()
        self.assertEqual(rc, 0)
        for cfg in (self.state.radarr_media, self.state.sonarr_media):
            self.assertTrue(cfg.get("enableCompletedDownloadHandling"))
            self.assertTrue(cfg.get("skipFreeSpaceCheckWhenImporting"))
            self.assertEqual(cfg.get("minimumFreeSpaceWhenImporting"), 100)
            self.assertFalse(cfg.get("copyUsingHardlinks"))
            self.assertTrue(cfg.get("importExtraFiles"))
            self.assertEqual(cfg.get("extraFileExtensions"), "srt")
        for cfg in (self.state.radarr_dl_config, self.state.sonarr_dl_config):
            self.assertTrue(cfg.get("enableCompletedDownloadHandling"))
            self.assertFalse(cfg.get("autoRedownloadFailed"))
            self.assertFalse(cfg.get("autoRedownloadFailedFromInteractiveSearch"))

    def test_media_management_400_does_not_block_wire(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.fail_media_management = True
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertEqual(names, {"Max", "Default", "Anything"})

    def test_refresh_imports_when_queue_is_stuck(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "stuck-refresh"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        (complete / "Stuck.mkv").write_bytes(b"ok")
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.queue = [
            {
                "title": "Stuck",
                "trackedDownloadState": "importPending",
                "trackedDownloadStatus": "warning",
                "statusMessages": [{"title": "Not enough free space", "messages": []}],
            }
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(
            [c.get("name") for c in self.state.arr_commands].count("RefreshMonitoredDownloads"),
            2,
        )

    def test_housekeep_logs_library_path_when_file_never_imported(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.state.movies = [
            {
                "id": 1,
                "title": "Marty Supreme",
                "monitored": True,
                "hasFile": False,
                "path": "/media/dlna/Movies/Not Kid Friendly/Marty Supreme (2025)",
            }
        ]
        self.state.queue = [
            {
                "title": "Marty Supreme",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "trackedDownloadState": "downloading",
                "outputPath": "/media/dlna/downloads/complete/Marty Supreme",
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn(
            "radarr download not in the library yet: Marty Supreme (downloading) "
            "qbit=/media/dlna/downloads/complete/Marty Supreme",
            out,
        )
        self.assertIn(
            "radarr titles with no library file yet: Marty Supreme -> "
            "/media/dlna/Movies/Not Kid Friendly/Marty Supreme (2025)",
            out,
        )

    def test_housekeep_logs_waiting_series_when_a_season_is_still_missing(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.state.series = [
            {
                "id": 10,
                "title": "Partial",
                "monitored": True,
                "path": "/media/dlna/TV/Not Kid Friendly/Partial",
                "statistics": {"episodeFileCount": 3, "episodeCount": 8},
            },
            {
                "id": 11,
                "title": "Caught up",
                "monitored": True,
                "path": "/media/dlna/TV/Not Kid Friendly/Caught up",
                "statistics": {"episodeFileCount": 8, "episodeCount": 8},
            },
        ]
        self.state.sonarr_queue = [
            {
                "title": "Partial",
                "status": "downloading",
                "trackedDownloadStatus": "ok",
                "trackedDownloadState": "downloading",
                "outputPath": "/media/dlna/downloads/complete/Partial.S02",
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn(
            "sonarr download not in the library yet: Partial (downloading) "
            "qbit=/media/dlna/downloads/complete/Partial.S02",
            out,
        )
        self.assertIn(
            "sonarr titles with no library file yet: Partial -> "
            "/media/dlna/TV/Not Kid Friendly/Partial",
            out,
        )
        self.assertNotIn("Caught up", out)

    def test_housekeep_logs_command_queue_and_missing_before_scan(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        started = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=14)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.state.sonarr_command_queue = [
            {
                "name": "EpisodeSearch",
                "status": "started",
                "started": started,
                "priority": "low",
                "body": {"seriesTitle": "Show", "episodeIds": [4]},
            },
            {
                "name": "EpisodeSearch",
                "status": "queued",
                "queued": started,
                "priority": "low",
                "body": {"episodeIds": [1, 2, 3]},
            },
        ]
        self.state.wanted_missing = [
            {
                "id": 4,
                "seasonNumber": 1,
                "episodeNumber": 4,
                "series": {"title": "Show"},
            },
            {
                "id": 3,
                "seasonNumber": 1,
                "episodeNumber": 3,
                "series": {"title": "Show"},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            rc = ws.housekeep()
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("complete/: 0 video(s) on disk before Refresh/Scan", out)
        self.assertIn("sonarr commands: started EpisodeSearch", out)
        self.assertIn("queued EpisodeSearch", out)
        self.assertIn("low (Show 1 episode(s))", out)
        self.assertIn("sonarr wanted/missing: 2 (Show S01E04, Show S01E03)", out)
        self.assertNotIn("sonarr checking completed downloads", out)
        diag = out.find("sonarr commands:")
        self.assertGreaterEqual(diag, 0)
        sonarr_command_calls = [
            call
            for call in self.state.calls
            if call[0] == "sonarr" and str(call[2]).endswith("/command")
        ]
        self.assertEqual(sonarr_command_calls[0][1], "GET")
        self.assertFalse(
            any(call[1] == "POST" for call in sonarr_command_calls),
            sonarr_command_calls,
        )
        self.assertFalse(
            any(item.get("name") == "RefreshMonitoredDownloads" for item in self.state.arr_commands)
        )
        self.assertFalse(
            any(item.get("name") == "EpisodeSearch" for item in self.state.arr_commands)
        )

    def test_housekeep_repeats_still_in_complete_only_when_set_changes(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "quiet-complete"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        (complete / "Stuck.mkv").write_bytes(b"x" * 100)
        os.environ["MEDIA_ROOT"] = str(root)
        from io import StringIO
        from contextlib import redirect_stdout

        first = StringIO()
        with redirect_stdout(first):
            self.assertEqual(ws.housekeep(), 0)
        first_out = first.getvalue()
        self.assertIn("still in complete/:", first_out)
        self.assertIn("Stuck.mkv", first_out)
        self.assertIn("complete/: 1 video(s) on disk before Refresh/Scan", first_out)
        self.assertIn("sonarr checking completed downloads", first_out)

        second = StringIO()
        with redirect_stdout(second):
            self.assertEqual(ws.housekeep(), 0)
        second_out = second.getvalue()
        self.assertNotIn("still in complete/:", second_out)
        self.assertIn("complete/: 1 video(s) on disk before Refresh/Scan", second_out)
        self.assertNotIn("sonarr checking completed downloads", second_out)

        (complete / "Other.mkv").write_bytes(b"y" * 100)
        third = StringIO()
        with redirect_stdout(third):
            self.assertEqual(ws.housekeep(), 0)
        third_out = third.getvalue()
        self.assertIn("still in complete/:", third_out)
        self.assertIn("Other.mkv", third_out)

    def test_retries_monitored_titles_still_missing_a_file(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.movies = [
            {"id": 99, "title": "Waiting", "monitored": True, "hasFile": False},
            {"id": 100, "title": "Done", "monitored": True, "hasFile": True},
        ]
        self.state.wanted_missing = [{"id": 44, "title": "S01E01"}]
        rc = ws.main()
        self.assertEqual(rc, 0)
        names = [c.get("name") for c in self.state.arr_commands]
        self.assertTrue(any(n.startswith("Movies") and n.endswith("Search") for n in names))
        movie_retry = next(c for c in self.state.arr_commands if str(c.get("name", "")).startswith("Movies"))
        self.assertEqual(movie_retry.get("movieIds"), [99])
        ep_retry = next(c for c in self.state.arr_commands if c.get("name") == "EpisodeSearch")
        self.assertEqual(ep_retry.get("episodeIds"), [44])

    def test_housekeep_retries_missing_episodes_once_per_id_set(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.wanted_missing = [
            {"id": 1, "seasonNumber": 3, "episodeNumber": 1, "series": {"title": "Silo"}},
            {"id": 2, "seasonNumber": 3, "episodeNumber": 2, "series": {"title": "Silo"}},
            {"id": 3, "seasonNumber": 3, "episodeNumber": 3, "series": {"title": "Silo"}},
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        first = StringIO()
        with redirect_stdout(first):
            self.assertEqual(ws.housekeep(), 0)
        searches = [
            item for item in self.state.arr_commands if item.get("name") == "EpisodeSearch"
        ]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0].get("episodeIds"), [1, 2, 3])
        self.assertIn("searching again for 3 missing episode(s)", first.getvalue())
        self.assertFalse(
            any(item.get("name") == "RefreshMonitoredDownloads" for item in self.state.arr_commands)
        )

        self.state.arr_commands.clear()
        second = StringIO()
        with redirect_stdout(second):
            self.assertEqual(ws.housekeep(), 0)
        self.assertEqual(
            [item for item in self.state.arr_commands if item.get("name") == "EpisodeSearch"],
            [],
        )
        self.assertNotIn("searching again for", second.getvalue())

        self.state.wanted_missing = self.state.wanted_missing[:2]
        third = StringIO()
        with redirect_stdout(third):
            self.assertEqual(ws.housekeep(), 0)
        searches = [
            item for item in self.state.arr_commands if item.get("name") == "EpisodeSearch"
        ]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0].get("episodeIds"), [1, 2])
        self.assertIn("searching again for 2 missing episode(s)", third.getvalue())

    def test_housekeep_skips_refresh_when_search_running_and_complete_has_videos(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "search-inflight"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S03E04.mkv"
        leftover.write_bytes(b"copy")
        os.environ["MEDIA_ROOT"] = str(root)
        tv = root / "TV" / "Not Kid Friendly" / "Silo"
        tv.mkdir(parents=True)
        (tv / "Silo.S03E04.mkv").write_bytes(b"library")
        self.state.sonarr_command_queue = [
            {
                "name": "EpisodeSearch",
                "status": "started",
                "priority": "low",
                "body": {"episodeIds": [1, 2, 3]},
            }
        ]
        self.state.wanted_missing = [
            {"id": 1, "seasonNumber": 3, "episodeNumber": 1, "series": {"title": "Silo"}},
        ]
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(tv)}
        ]
        self.state.episodes = [
            {"id": 14, "seriesId": 10, "hasFile": True, "title": "The Harmless"},
        ]
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.sonarr_manual_import = [
            {
                "path": str(leftover),
                "seriesId": 10,
                "episodeIds": [14],
                "series": {"id": 10, "title": "Silo", "path": str(tv)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [],
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        names = [item.get("name") for item in self.state.arr_commands]
        self.assertNotIn("RefreshMonitoredDownloads", names)
        self.assertNotIn("EpisodeSearch", names)
        self.assertIn("DownloadedEpisodesScan", names)
        self.assertFalse(leftover.exists())
        self.assertIn(
            "not refreshing completed downloads while a search is running",
            buf.getvalue(),
        )

    def test_points_existing_prowlarr_apps_at_title_search_proxy(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.apps = [
            {
                "id": 3,
                "name": "Radarr",
                "fields": [
                    {"name": "prowlarrUrl", "value": "http://127.0.0.1:9696"},
                    {"name": "baseUrl", "value": "http://127.0.0.1:7878"},
                    {"name": "apiKey", "value": "radarr-key"},
                ],
            }
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        radarr = next(item for item in self.state.apps if item["name"] == "Radarr")
        fields = {f["name"]: f.get("value") for f in radarr.get("fields") or []}
        self.assertEqual(fields.get("prowlarrUrl"), "http://127.0.0.1:9698")
        self.assertEqual(fields.get("syncCategories"), ws.RADARR_SYNC_CATS)

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
        local_posts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "POST" and call[2] == "/api/v1/auth/local"
        ]
        self.assertEqual(local_posts, [])
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
        local_posts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "POST" and call[2] == "/api/v1/auth/local"
        ]
        self.assertEqual(local_posts, [])
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
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Default")
        self.assertEqual(self.state.seerr_sonarr[0]["activeProfileName"], "Default")
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
        local_posts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "POST" and call[2] == "/api/v1/auth/local"
        ]
        self.assertEqual(len(local_posts), 1)

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
        self.assertEqual(dests["Kid Show"], "/media/TV/Kid Friendly")
        self.assertEqual(dests["Kid Pathless"], "/media/TV/Kid Friendly")
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
