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
import time
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
recyclarr = load("recyclarr_sync", BIN / "recyclarr-sync")
arrp = load("prowlarr_arr_proxy", BIN / "prowlarr-arr-proxy")
rr = load("route_rating", BIN / "route-rating")
wqc = load("wg_quick_contract", ROOT / "tests/lib/wg_quick_contract.py")
emitmod = load("pompey_log_emit", BIN / "pompey-log-emit")
vpnstats = load("pompey_vpn_stats", BIN / "pompey-vpn-stats")
dbginc = load("write_debug_ingress", BIN / "write-debug-ingress")


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
            "language": {"id": 1, "name": "English"},
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
        self.folder_ids: dict[str, int] = {}
        self.next_folder_id = 1
        self.download_clients: list[dict] = []
        self.radarr_clients: list[dict] = []
        self.sonarr_clients: list[dict] = []
        self.prowlarr_clients: list[dict] = []
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
        self.arr_drop_v3 = False
        self.reject_command_names: set[str] = set()
        self.seerr_missing_jobs: set[str] = set()
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
            "recycleBin": "",
            "recycleBinCleanupDays": 7,
            "deleteEmptyFolders": True,
            "useScriptImport": False,
            "autoUnmonitorPreviouslyDownloadedMovies": True,
        }
        self.sonarr_media: dict = {
            "id": 1,
            "enableCompletedDownloadHandling": True,
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 100,
            "copyUsingHardlinks": True,
            "recycleBin": "",
            "recycleBinCleanupDays": 7,
            "deleteEmptyFolders": True,
            "useScriptImport": False,
            "autoUnmonitorPreviouslyDownloadedEpisodes": True,
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
        self.radarr_wanted_cutoff: list[dict] = []
        self.sonarr_wanted_cutoff: list[dict] = []
        self.seerr_requests: list[dict] = []
        self.seerr_fail_requests = False
        self.queue: list[dict] = []
        self.sonarr_queue: list[dict] = []
        self.cancelled_commands: list[dict] = []
        self.aborted_queue: list[dict] = []
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
            {"id": 1, "title": "Kid Flick", "certification": "PG", "path": "/media/Movies/By Rating/Kid Flick"},
            {"id": 2, "title": "Unknown", "certification": "", "path": "/media/Movies/By Rating/Unknown"},
            {"id": 3, "title": "Already Kid", "certification": "G", "path": "/media/Movies/Kid Friendly/Already Kid"},
            {
                "id": 4,
                "title": "Nested Kid",
                "ratings": {"tmdb": {"certification": "PG"}},
                "path": "/media/Movies/By Rating/Nested Kid",
            },
        ]
        self.import_lists: list[dict] = []
        self.series = [
            {"id": 10, "title": "Adult Show", "certification": "TV-MA", "path": "/media/TV/By Rating/Adult Show"},
            {
                "id": 11,
                "title": "Kid Show",
                "certification": "TV-PG",
                "path": "/media/TV/By Rating/Kid Show",
            },
            {
                "id": 12,
                "title": "Kid Pathless",
                "certification": "TV-Y",
                "rootFolderPath": "/media/TV/By Rating/Kid Pathless",
            },
        ]
        self.moved: list[dict] = []


def handler_for(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):
            return

        def _editor(self, items, ids, body):
            id_set = {int(i) for i in ids if i is not None}
            payload = body if isinstance(body, dict) else {}
            for i, item in enumerate(items):
                try:
                    current = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                if current not in id_set:
                    continue
                saved = dict(item)
                if payload.get("qualityProfileId") is not None:
                    saved["qualityProfileId"] = payload["qualityProfileId"]
                root = payload.get("rootFolderPath")
                if root:
                    root = str(root).rstrip("/")
                    path = str(item.get("path") or item.get("rootFolderPath") or "").rstrip("/")
                    name = path.rsplit("/", 1)[-1] if path else ""
                    saved["rootFolderPath"] = root
                    if name:
                        saved["path"] = f"{root}/{name}"
                items[i] = saved
                state.moved.append(saved)
            return self._send(body=payload)

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
                if state.arr_drop_v3 and "/api/v3/" in path:
                    return self._send(404, {"error": path})
                folders = state.sonarr_folders if role == "sonarr" else state.radarr_folders
                profiles = state.radarr_profiles if role == "radarr" else state.sonarr_profiles
                defs = state.radarr_defs if role == "radarr" else state.sonarr_defs
                formats = state.radarr_formats if role == "radarr" else state.sonarr_formats
                if path == "/ping":
                    return self._send(body={"status": "OK"})
                if path.endswith("/rootfolder") and method == "GET":
                    rows = []
                    for folder_path in folders:
                        key = str(folder_path or "").rstrip("/")
                        if key not in state.folder_ids:
                            state.folder_ids[key] = state.next_folder_id
                            state.next_folder_id += 1
                        rows.append({"id": state.folder_ids[key], "path": folder_path})
                    return self._send(body=rows)
                if path.endswith("/rootfolder") and method == "POST":
                    folders.append((body or {}).get("path"))
                    return self._send(201, body)
                if "/rootfolder/" in path and method == "DELETE":
                    try:
                        ident = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    path_by_id = {fid: folder for folder, fid in state.folder_ids.items()}
                    leftover = path_by_id.get(ident)
                    if leftover is None:
                        return self._send(404, {"error": path})
                    titles = state.movies if role == "radarr" else state.series
                    still = [
                        item
                        for item in titles
                        if ws.in_root(
                            str(item.get("path") or item.get("rootFolderPath") or ""),
                            leftover,
                        )
                        and not any(
                            ws.in_root(
                                str(item.get("path") or item.get("rootFolderPath") or ""),
                                wanted,
                            )
                            for wanted in folders
                            if str(wanted).rstrip("/") != leftover
                        )
                    ]
                    if still:
                        return self._send(
                            400,
                            {"message": f"Root folder {leftover} is in use"},
                        )
                    folders[:] = [
                        folder
                        for folder in folders
                        if str(folder).rstrip("/") != leftover
                    ]
                    state.folder_ids.pop(leftover, None)
                    return self._send(200)
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
                if "/queue/" in path and method == "DELETE":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    rows = state.queue if role == "radarr" else state.sonarr_queue
                    state.aborted_queue.append(
                        {
                            "role": role,
                            "id": idx,
                            "removeFromClient": (query.get("removeFromClient") or [""])[0],
                            "blocklist": (query.get("blocklist") or [""])[0],
                            "skipRedownload": (query.get("skipRedownload") or [""])[0],
                        }
                    )
                    leftover = [item for item in rows if item.get("id") != idx]
                    if role == "radarr":
                        state.queue = leftover
                    else:
                        state.sonarr_queue = leftover
                    return self._send(200, {})
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
                    titles = state.movies if role == "radarr" else state.series
                    if any(item.get("qualityProfileId") == idx for item in titles):
                        return self._send(
                            400,
                            {"message": f"Profile {idx} is in use by existing titles"},
                        )
                    if any(item.get("qualityProfileId") == idx for item in state.import_lists):
                        return self._send(
                            400,
                            {"message": f"Profile {idx} is in use by an import list"},
                        )
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
                if path.endswith("/importlist") and method == "GET":
                    return self._send(body=state.import_lists)
                if "/importlist/" in path and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(state.import_lists):
                        if item.get("id") == idx:
                            saved = dict(item)
                            saved.update(body or {})
                            saved["id"] = idx
                            state.import_lists[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
                if path.endswith("/movie/editor") and method == "PUT":
                    return self._editor(state.movies, (body or {}).get("movieIds") or [], body)
                if path.endswith("/series/editor") and method == "PUT":
                    return self._editor(state.series, (body or {}).get("seriesIds") or [], body)
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
                    if isinstance(body, dict) and body.get("id") is not None:
                        for i, movie in enumerate(state.movies):
                            if movie.get("id") == body.get("id"):
                                saved = dict(movie)
                                saved.update(body)
                                state.movies[i] = saved
                                return self._send(body=saved)
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
                if path.endswith("/wanted/cutoff") and method == "GET":
                    rows = (
                        state.radarr_wanted_cutoff
                        if role == "radarr"
                        else state.sonarr_wanted_cutoff
                    )
                    return self._send(
                        body={
                            "records": rows,
                            "page": 1,
                            "pageSize": 50,
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
                    if isinstance(body, dict) and body.get("id") is not None:
                        for i, show in enumerate(state.series):
                            if show.get("id") == body.get("id"):
                                saved = dict(show)
                                saved.update(body)
                                state.series[i] = saved
                                return self._send(body=saved)
                    return self._send(body=body)
                if "/command/" in path and method == "DELETE":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    queued = (
                        state.radarr_command_queue
                        if role == "radarr"
                        else state.sonarr_command_queue
                    )
                    state.cancelled_commands.append({"role": role, "id": idx})
                    leftover = [item for item in queued if item.get("id") != idx]
                    if role == "radarr":
                        state.radarr_command_queue = leftover
                    else:
                        state.sonarr_command_queue = leftover
                    return self._send(200, {})
                if path.endswith("/command") and method == "GET":
                    queued = (
                        state.radarr_command_queue
                        if role == "radarr"
                        else state.sonarr_command_queue
                    )
                    return self._send(body=queued)
                if path.endswith("/command") and method == "POST":
                    name = (body or {}).get("name") if isinstance(body, dict) else None
                    if name in state.reject_command_names:
                        return self._send(400, {"message": f"unknown command {name}"})
                    state.arr_commands.append(body or {})
                    return self._send(201, body or {})
                if path.endswith("/indexer") and method == "GET":
                    listed = state.radarr_indexers if role == "radarr" else state.sonarr_indexers
                    return self._send(body=listed)
                if "/indexer/" in path and method == "GET":
                    listed = state.radarr_indexers if role == "radarr" else state.sonarr_indexers
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for item in listed:
                        if item.get("id") == idx:
                            return self._send(body=item)
                    return self._send(404, {"error": path})
                if "/indexer/" in path and method == "PUT":
                    listed = state.radarr_indexers if role == "radarr" else state.sonarr_indexers
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(listed):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            listed[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
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
                if path == "/api/v1/downloadclient" and method == "GET":
                    return self._send(body=state.prowlarr_clients)
                if path == "/api/v1/downloadclient/schema":
                    return self._send(
                        body=[
                            {
                                "implementation": "QBittorrent",
                                "fields": [
                                    {"name": n}
                                    for n in (
                                        "host",
                                        "port",
                                        "username",
                                        "password",
                                        "movieCategory",
                                        "tvCategory",
                                        "musicCategory",
                                        "bookCategory",
                                        "category",
                                        "useSsl",
                                    )
                                ],
                            }
                        ]
                    )
                if path == "/api/v1/downloadclient" and method == "POST":
                    posted = dict(body or {})
                    posted.setdefault("id", len(state.prowlarr_clients) + 1)
                    state.prowlarr_clients.append(posted)
                    return self._send(201, posted)
                if path.startswith("/api/v1/downloadclient/") and method == "PUT":
                    try:
                        idx = int(path.rsplit("/", 1)[-1])
                    except ValueError:
                        return self._send(404, {"error": path})
                    for i, item in enumerate(state.prowlarr_clients):
                        if item.get("id") == idx:
                            saved = dict(body or item)
                            saved["id"] = idx
                            state.prowlarr_clients[i] = saved
                            return self._send(body=saved)
                    return self._send(404, {"error": path})
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
                    job = path.rsplit("/", 2)[-2]
                    if job in state.seerr_missing_jobs:
                        return self._send(404, {"error": path})
                    state.seerr_jobs.append(job)
                    return self._send(body={"ok": True})
                if path == "/api/v1/request" and method == "GET":
                    if state.seerr_fail_requests:
                        return self._send(500, {"message": "requests failed"})
                    skip = int((query.get("skip") or ["0"])[0] or 0)
                    take = int((query.get("take") or ["50"])[0] or 50)
                    rows = state.seerr_requests[skip : skip + take]
                    return self._send(
                        body={
                            "pageInfo": {
                                "pages": 1,
                                "pageSize": take,
                                "results": len(state.seerr_requests),
                                "page": 1,
                            },
                            "results": rows,
                        }
                    )
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
        self.assertTrue(rr.kid_cert("TV-Y", rr.KID_TV))
        self.assertTrue(rr.kid_cert("TV-Y7", rr.KID_TV))
        self.assertTrue(rr.kid_cert("TV-G", rr.KID_TV))
        self.assertFalse(rr.kid_cert("R", rr.KID_MOVIE))
        self.assertFalse(rr.kid_cert("", rr.KID_MOVIE))
        self.assertFalse(rr.kid_cert("TV-MA", rr.KID_TV))
        self.assertFalse(rr.kid_cert("TV-14", rr.KID_TV))

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
        self.assertTrue(ws.in_root("/media/TV/Show", "/media/TV"))
        self.assertFalse(ws.in_root("/media/TV Extra/Show", "/media/TV"))

    def test_leftover_root_dest_matches_old_movie_and_tv_defaults(self):
        """0.2.20 `/media` + flat names, plus a flattened Not Kid path."""
        tv_auto, tv_gen, tv_kid = (
            "/media/dlna/TV/By Rating",
            "/media/dlna/TV/Not Kid Friendly",
            "/media/dlna/TV/Kid Friendly",
        )
        movie_auto, movie_gen, movie_kid = (
            "/media/dlna/Movies/By Rating",
            "/media/dlna/Movies/Not Kid Friendly",
            "/media/dlna/Movies/Kid Friendly",
        )
        self.assertEqual(ws.leftover_root_dest("/media/TV", tv_kid, tv_gen, tv_auto), tv_auto)
        self.assertEqual(
            ws.leftover_root_dest("/media/Kid Friendly TV", tv_kid, tv_gen, tv_auto),
            tv_kid,
        )
        self.assertEqual(
            ws.leftover_root_dest("/media/dlna/TV Not Kid Friendly", tv_kid, tv_gen, tv_auto),
            tv_gen,
        )
        self.assertEqual(
            ws.leftover_root_dest("/media/Movies", movie_kid, movie_gen, movie_auto),
            movie_auto,
        )
        self.assertEqual(
            ws.leftover_root_dest(
                "/media/Kid Friendly Movies", movie_kid, movie_gen, movie_auto
            ),
            movie_kid,
        )
        self.assertEqual(
            ws.leftover_root_dest(
                "/media/dlna/Movies Not Kid Friendly", movie_kid, movie_gen, movie_auto
            ),
            movie_gen,
        )

    def test_title_on_leftover_skips_wanted_child_roots(self):
        wanted = {"/media/Movies/By Rating", "/media/Movies/Kid Friendly"}
        self.assertTrue(
            ws.title_on_leftover_root("/media/Movies/Old Title", "/media/Movies", wanted)
        )
        self.assertFalse(
            ws.title_on_leftover_root(
                "/media/Movies/By Rating/Kid Flick", "/media/Movies", wanted
            )
        )

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
            self.assertEqual(rr.movies_auto_dir(), "/media/dlna/Movies/By Rating")
            self.assertEqual(rr.tv_auto_dir(), "/media/dlna/TV/By Rating")
            self.assertEqual(ws.movies_auto_dir(), "/media/dlna/Movies/By Rating")
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
        self.assertIn("debug-consoles", html)
        self.assertIn("debug/radarr/", html)
        self.assertIn("debug/sonarr/", html)
        self.assertIn("debug/qbittorrent/", html)
        self.assertIn("Interactive Search", html)
        shim = (ROOT / "pompey/rootfs/usr/share/pompey/debug-shim.js").read_text()
        self.assertIn("window.fetch", shim)
        self.assertIn("XMLHttpRequest", shim)
        self.assertIn('url.charAt(0) === "/"', shim)
        self.assertIn("data.debug", html)
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
        self.assertIn("sawDashboard", html)
        self.assertIn("is-ready", html)
        self.assertIn("isDashboard", html)
        self.assertIn("vpn-bw", html)
        self.assertIn("renderVpn", html)
        self.assertIn("vpn-graph", html)
        self.assertIn("data.vpn", html)
        cfg = (ROOT / "pompey/config.yaml").read_text()
        self.assertIn("5055/tcp: 5055", cfg)
        self.assertIn("9696/tcp: 9696", cfg)
        self.assertIn("debug: false", cfg)
        self.assertIn("debug: bool", cfg)
        self.assertIn("simultaneous_downloads: 8", cfg)
        self.assertIn("simultaneous_downloads: int(1,20)", cfg)
        self.assertNotIn("7878/tcp", cfg)
        self.assertNotIn("8989/tcp", cfg)
        self.assertNotIn("8080/tcp", cfg)
        seerr = (ROOT / "pompey/rootfs/etc/services.d/seerr/run").read_text()
        self.assertIn("HOST=0.0.0.0", seerr)
        self.assertNotIn("HOST=127.0.0.1", seerr)
        docker = (ROOT / "pompey/Dockerfile").read_text()
        self.assertNotIn("plex", docker.lower())
        self.assertIn("\n    git \\\n", docker)
        self.assertIn("\n    xz \\\n", docker)
        self.assertNotIn("nginx-mod-http-sub", docker)
        self.assertFalse((ROOT / "pompey/rootfs/usr/local/bin/pompey-ingress").exists())
        self.assertFalse((ROOT / "pompey/rootfs/etc/services.d/ingress-proxy").exists())
        self.assertFalse((ROOT / "tests/preview_seerr_ingress.py").exists())
        self.assertNotIn("keep_ingress_as_pompey", (BIN / "wire-stack").read_text())
        seerr_run = (ROOT / "pompey/rootfs/etc/services.d/seerr/run").read_text()
        fetch = (ROOT / "pompey/rootfs/usr/local/bin/fetch-engines").read_text()
        self.assertIn("recyclarr-linux-musl-", fetch)
        self.assertIn("POMPEY_SKIP_RECYCLARR", fetch)
        self.assertIn("identity_current", fetch)
        self.assertIn("engines-checked", fetch)
        self.assertIn("POMPEY_HOLD_QBIT", fetch)
        self.assertNotIn("already present", fetch)
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

    def test_wire_stack_never_asks_arr_to_delete_library_titles(self):
        src = (BIN / "wire-stack").read_text()
        self.assertNotIn('"deleteFiles": "true"', src)
        self.assertNotIn("DeleteMovie", src)
        self.assertNotIn("DeleteSeries", src)
        self.assertIn('"deleteFiles": "false"', src)
        self.assertIn("recycleBin", src)

    def test_wire_keeps_housekeeping_hidden_qbit(self):
        wire = (ROOT / "pompey/rootfs/etc/services.d/wire/run").read_text()
        self.assertIn("housekeep", wire)
        self.assertIn("closeout", wire)
        src = (ROOT / "pompey/rootfs/usr/local/bin/wire-stack").read_text()
        self.assertIn('"deleteFiles": "false"', src)
        self.assertNotIn('"deleteFiles": "true"', src)
        self.assertIn("apply_qbit_queue", src)
        self.assertIn("dont_count_slow_torrents", src)

    def test_download_scan_paths_include_legacy_category_folders(self):
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.assertEqual(
            ws.download_scan_paths(),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/radarr",
                "/media/dlna/downloads/complete/sonarr",
                "/media/dlna/downloads/manual",
            ],
        )
        self.assertEqual(
            ws.download_scan_paths("radarr"),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/radarr",
                "/media/dlna/downloads/manual",
            ],
        )
        self.assertEqual(
            ws.download_scan_paths("sonarr"),
            [
                "/media/dlna/downloads/complete",
                "/media/dlna/downloads/complete/sonarr",
                "/media/dlna/downloads/manual",
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

    def test_safe_to_delete_complete_path_never_touches_library(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            complete = base / "downloads" / "complete"
            manual = base / "downloads" / "manual"
            movies = base / "Movies" / "Not Kid Friendly" / "Silo"
            complete.mkdir(parents=True)
            manual.mkdir(parents=True)
            movies.mkdir(parents=True)
            leftover = complete / "Silo.S03E01.mkv"
            leftover.write_bytes(b"copy")
            grab = manual / "Show.S01E01.mkv"
            grab.write_bytes(b"human")
            library = movies / "Silo.S03E01.mkv"
            library.write_bytes(b"library")
            old = {key: os.environ.get(key) for key in ("MEDIA_ROOT", "MEDIA_MOVIES")}
            os.environ["MEDIA_ROOT"] = str(base)
            os.environ["MEDIA_MOVIES"] = "Movies/Not Kid Friendly"
            try:
                self.assertTrue(ws.safe_to_delete_complete_path(str(leftover)))
                self.assertTrue(ws.safe_to_delete_complete_path(str(grab)))
                self.assertFalse(ws.safe_to_delete_complete_path(str(complete)))
                self.assertFalse(ws.safe_to_delete_complete_path(str(manual)))
                self.assertFalse(ws.safe_to_delete_complete_path(str(library)))
                self.assertFalse(ws.safe_to_delete_complete_path(str(movies)))
                self.assertTrue(ws.path_is_library(str(library)))
                self.assertFalse(ws.complete_overlaps_library())
                ws.remove_complete_leftover(str(library), "should not delete")
                self.assertTrue(library.is_file())
                os.environ["MEDIA_MOVIES"] = "downloads/complete"
                self.assertTrue(ws.complete_overlaps_library())
                self.assertFalse(ws.safe_to_delete_complete_path(str(leftover)))
                self.assertTrue(ws.safe_to_delete_complete_path(str(grab)))
                ws.remove_complete_leftover(str(leftover), "overlap")
                self.assertTrue(leftover.is_file())
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

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

    def test_season_and_episode_searches_groups_a_holey_season(self):
        seasons, leftover = ws.season_and_episode_searches(
            [
                {
                    "id": 1,
                    "seriesId": 10,
                    "seasonNumber": 1,
                    "series": {"id": 10, "title": "World Trigger"},
                },
                {
                    "id": 2,
                    "seriesId": 10,
                    "seasonNumber": 1,
                    "series": {"id": 10, "title": "World Trigger"},
                },
                {
                    "id": 3,
                    "seriesId": 10,
                    "seasonNumber": 1,
                    "series": {"id": 10, "title": "World Trigger"},
                },
                {
                    "id": 9,
                    "seriesId": 11,
                    "seasonNumber": 2,
                    "series": {"id": 11, "title": "Silo"},
                },
                {"id": 12, "title": "no-season-row"},
            ]
        )
        self.assertEqual(seasons, [(10, 1)])
        self.assertEqual(leftover, [12, 9])

    def test_after_download_defaults_to_stop_sharing(self):
        os.environ.pop("AFTER_DOWNLOAD", None)
        self.assertEqual(ws.after_download(), "stop_sharing")
        os.environ["AFTER_DOWNLOAD"] = "share_to_ratio"
        self.assertEqual(ws.after_download(), "share_to_ratio")
        os.environ["AFTER_DOWNLOAD"] = "share-one-day"
        self.assertEqual(ws.after_download(), "share_one_day")
        os.environ.pop("AFTER_DOWNLOAD", None)

    def test_simultaneous_downloads_clamps_and_defaults(self):
        os.environ.pop("SIMULTANEOUS_DOWNLOADS", None)
        self.assertEqual(ws.simultaneous_downloads(), 8)
        os.environ["SIMULTANEOUS_DOWNLOADS"] = "3"
        self.assertEqual(ws.simultaneous_downloads(), 3)
        os.environ["SIMULTANEOUS_DOWNLOADS"] = "99"
        self.assertEqual(ws.simultaneous_downloads(), 20)
        os.environ["SIMULTANEOUS_DOWNLOADS"] = "0"
        self.assertEqual(ws.simultaneous_downloads(), 1)
        os.environ["SIMULTANEOUS_DOWNLOADS"] = "nope"
        self.assertEqual(ws.simultaneous_downloads(), 8)
        os.environ.pop("SIMULTANEOUS_DOWNLOADS", None)

    def test_qbit_client_values_keep_arr_and_prowlarr_apart(self):
        secrets = {"qbit_user": "pompey", "qbit_password": "secret"}
        radarr_cat, radarr = ws.qbit_client_values(secrets, "radarr")
        sonarr_cat, sonarr = ws.qbit_client_values(secrets, "sonarr")
        prow_cat, prow = ws.qbit_client_values(secrets, "prowlarr")
        self.assertEqual(radarr_cat, "radarr")
        self.assertEqual(sonarr_cat, "sonarr")
        self.assertEqual(prow_cat, "prowlarr")
        self.assertEqual(radarr["movieCategory"], "radarr")
        self.assertEqual(sonarr["tvCategory"], "sonarr")
        self.assertEqual(prow["category"], "prowlarr")
        self.assertEqual(prow["movieCategory"], "prowlarr")
        self.assertEqual(prow["tvCategory"], "prowlarr")
        self.assertNotIn("musicCategory", radarr)
        self.assertEqual(prow["musicCategory"], "prowlarr")
        with self.assertRaises(ValueError):
            ws.qbit_client_values(secrets, "sabnzbd")

    def test_qbit_queue_preferences_ignore_slow_and_total_cap(self):
        prefs = ws.qbit_queue_preferences(3)
        self.assertTrue(prefs["queueing_enabled"])
        self.assertEqual(prefs["max_active_downloads"], 3)
        self.assertEqual(prefs["max_active_uploads"], 3)
        self.assertEqual(prefs["max_active_torrents"], 20)
        self.assertTrue(prefs["dont_count_slow_torrents"])
        self.assertEqual(prefs["slow_torrent_dl_rate_threshold"], 2)
        self.assertEqual(prefs["slow_torrent_ul_rate_threshold"], 2)
        self.assertEqual(prefs["slow_torrent_inactive_timer"], 180)
        prefs = ws.qbit_queue_preferences(8)
        self.assertEqual(prefs["max_active_torrents"], 24)

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
        self.assertTrue(ws.is_hard_import_reject("Unknown Series"))
        self.assertTrue(ws.is_hard_import_reject("Sample"))
        self.assertTrue(ws.is_hard_import_reject("File is a sample"))
        self.assertFalse(ws.is_hard_import_reject("Not a wanted quality for Default"))
        self.assertFalse(
            ws.is_hard_import_reject("Custom Format score of 0 does not meet minimum of 10")
        )
        self.assertFalse(
            ws.is_hard_import_reject("Existing file is of equal or higher quality")
        )
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
        self.assertEqual(
            ws.release_dir_under_complete(
                "/media/dlna/downloads/manual/www.UIndex.org - Title/file.mp4"
            ),
            "/media/dlna/downloads/manual/www.UIndex.org - Title",
        )
        self.assertEqual(
            ws.release_dir_under_complete("/media/dlna/downloads/manual/file.mp4"),
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
        groups_spec = (
            ("WEB 1080p", ("WEBDL-1080p", "WEBRip-1080p")),
            ("Bluray-1080p", ("Bluray-1080p",)),
        )
        items = ws.rebuild_profile_items(catalog, groups_spec)
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

    def test_title_quality_profile_helpers(self):
        self.assertEqual(ws.arr_title_collection("radarr"), "movie")
        self.assertEqual(ws.arr_title_collection("sonarr"), "series")
        self.assertEqual(ws.title_quality_profile_id({"qualityProfileId": 2}), 2)
        self.assertIsNone(ws.title_quality_profile_id({"id": 1}))
        self.assertEqual(ws.title_id_of({"id": 7, "qualityProfileId": 2}), 7)

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
                "NzbDrone.Common.Disk.DiskException: Unable to write to folder",
            ],
        )
        combined = out + err
        self.assertNotIn("at NzbDrone", combined)
        self.assertNotIn("at System.", combined)
        self.assertNotIn("at Arr.", combined)
        self.assertIn("Arr.Common.Disk.DiskException", err)
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
                "[Warn] DiskScanService: Folder is empty apikey=supersecretkey",
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

    def test_drops_indexer_http_flakes(self):
        out, err = self.captured(
            "Prowlarr",
            [
                "[Info] ReleaseSearchService: Searching indexer(s): [LimeTorrents] for Term: []",
                "[Warn] HttpClient: HTTP Error - Res: HTTP/1.1 [POST] http://127.0.0.1:7878/api/v3/indexer: 400.BadRequest",
                "[Warn] RadarrV3Proxy: No Results in configured categories",
                "[Error] Cardigann: Cloudflare protection detected for [Torrent Downloads]",
                "HTTP request failed: [429:TooManyRequests] [GET] at [http://127.0.0.1:9698/4/api]",
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            ],
        )
        combined = out + err
        self.assertNotIn("ReleaseSearchService", combined)
        self.assertNotIn("HTTP Error", combined)
        self.assertNotIn("No Results in configured categories", combined)
        self.assertNotIn("Cloudflare", combined)
        self.assertNotIn("TooManyRequests", combined)
        self.assertNotIn("<?xml", combined)

    def test_drops_consecutive_duplicate_lines(self):
        _, err = self.captured(
            "Radarr",
            [
                "[Warn] DiskScanService: Folder is empty",
                "[Warn] DiskScanService: Folder is empty",
            ],
        )
        self.assertEqual(err.count("Folder is empty"), 1)


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


class PlexBonusFiles(unittest.TestCase):
    def test_classifies_plex_extras_specials_episodes_and_samples(self):
        cases = [
            ("/c/Silo.S03/Extras/Making Of.mkv", "extra", "Other"),
            ("/c/Silo.S03/Featurettes/The Look.mkv", "extra", "Featurettes"),
            ("/c/Silo.S03/Silo.S03.Behind.The.Scenes.mkv", "extra", "Behind The Scenes"),
            ("/c/Silo.S01E01-behindthescenes.mkv", "extra", "Behind The Scenes"),
            ("/c/Silo.S00E01.Christmas.Special.1080p.mkv", "special", ""),
            ("/c/Silo.S03/Specials/Holiday Special.mkv", "special", ""),
            ("/c/Silo/Season 00/Christmas Special.mkv", "special", ""),
            ("/c/Silo/Season 0/Gag Reel.mkv", "special", ""),
            ("/c/Silo.S01E01.Who.Are.You.mkv", "episode", ""),
            ("/c/Silo.S01E01.The.Special.One.mkv", "episode", ""),
            ("/c/The.Interview.S01/The.Interview.S01E01.mkv", "episode", ""),
            ("/c/Silo.S03/Sample/silo.sample.mkv", "sample", ""),
            ("/c/Silo.S03/random-clip.mkv", "skip", ""),
        ]
        for path, kind, folder in cases:
            got_kind, got_folder, _suffix = ws.classify_bonus_video(path)
            self.assertEqual((got_kind, got_folder), (kind, folder), path)

    def test_specials_folder_reuses_season_00(self):
        tmp = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-specials-{os.getpid()}"
        show = tmp / "Silo"
        season00 = show / "Season 00"
        season00.mkdir(parents=True)
        self.assertEqual(ws.specials_dest_dir(str(show)), str(season00))
        other = tmp / "Other Show"
        self.assertEqual(
            ws.specials_dest_dir(str(other)),
            str(other / "Specials"),
        )

    def test_extras_dest_dir_uses_season_folder_including_season_zero(self):
        tmp = Path(os.environ.get("TEST_TMP") or "/tmp") / f"pompey-extradest-{os.getpid()}"
        show = tmp / "Silo"
        season03 = show / "Season 03"
        season00 = show / "Season 00"
        season03.mkdir(parents=True)
        season00.mkdir(parents=True)
        self.assertEqual(
            ws.extras_dest_dir(str(show), "Behind The Scenes", 3),
            str(season03 / "Behind The Scenes"),
        )
        self.assertEqual(
            ws.extras_dest_dir(str(show), "Interviews", 0),
            str(season00 / "Interviews"),
        )
        bare = tmp / "Bare Show"
        self.assertEqual(
            ws.extras_dest_dir(str(bare), "Featurettes", 0),
            str(bare / "Featurettes"),
        )
        self.assertEqual(
            ws.extras_dest_dir(str(bare), "Featurettes", 3),
            str(bare / "Featurettes"),
        )

    def test_bonus_display_title_strips_release_junk(self):
        self.assertEqual(
            ws.bonus_display_title(
                "Silo.S00E01.Christmas.Special.1080p.WEBRip.mkv",
                "Silo",
            ),
            "Christmas Special",
        )
        self.assertEqual(ws.bonus_display_title("Cast Interview.mkv", "Silo"), "Cast Interview")

    def test_best_arr_title_match_uses_longest_arr_title(self):
        rows = [
            {"id": 1, "title": "Silo", "path": "/tv/Silo"},
            {"id": 2, "title": "The Silo Files", "path": "/tv/The Silo Files"},
            {"id": 3, "title": "Unknown", "path": "/movies/Unknown"},
        ]
        hit = ws.best_arr_title_match("The.Silo.Files.S00.Specials", rows)
        self.assertEqual(hit["id"], 2)
        self.assertIsNone(ws.best_arr_title_match("Unrelated.S01", rows))
        self.assertIsNone(ws.best_arr_title_match("UnknownShow.S01.Behind.The.Scenes", rows))


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
        os.environ.pop("POMPEY_RECYCLARR", None)
        os.environ.pop("POMPEY_RECYCLARR_DATA", None)
        os.environ.pop("POMPEY_FETCH_ENGINES", None)
        os.environ.pop("POMPEY_ENGINE_REFRESH_AGE", None)
        os.environ.pop("POMPEY_HOLD_QBIT", None)
        os.environ.pop("POMPEY_WIRE_TIMEOUT", None)
        os.environ.pop("SIMULTANEOUS_DOWNLOADS", None)
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
            {"/media/TV/By Rating", "/media/TV/Not Kid Friendly", "/media/TV/Kid Friendly"},
        )
        self.assertEqual(
            set(self.state.radarr_folders),
            {"/media/Movies/By Rating", "/media/Movies/Not Kid Friendly", "/media/Movies/Kid Friendly"},
        )
        self.assertEqual(len(self.state.download_clients), 2)
        for client in self.state.download_clients:
            self.assertTrue(client.get("removeCompletedDownloads"))
            self.assertTrue(client.get("removeFailedDownloads"))
            fields = {f["name"]: f.get("value") for f in client.get("fields") or []}
            self.assertIn(fields.get("movieCategory") or fields.get("tvCategory"), {"radarr", "sonarr"})
            self.assertNotEqual(fields.get("movieCategory"), "prowlarr")
            self.assertNotEqual(fields.get("tvCategory"), "prowlarr")
        self.assertEqual(len(self.state.prowlarr_clients), 1)
        prow_client = self.state.prowlarr_clients[0]
        self.assertFalse(prow_client.get("removeCompletedDownloads"))
        self.assertTrue(prow_client.get("removeFailedDownloads"))
        prow_fields = {f["name"]: f.get("value") for f in prow_client.get("fields") or []}
        self.assertEqual(prow_fields.get("category"), "prowlarr")
        self.assertEqual(prow_fields.get("movieCategory"), "prowlarr")
        self.assertEqual(prow_fields.get("tvCategory"), "prowlarr")
        self.assertEqual(self.state.qbit_categories.get("radarr"), "/media/downloads/complete")
        self.assertEqual(self.state.qbit_categories.get("sonarr"), "/media/downloads/complete")
        self.assertEqual(self.state.qbit_categories.get("prowlarr"), "/media/downloads/manual")
        prefs_body = self.state.qbit_prefs
        self.assertIsInstance(prefs_body, dict)
        raw = prefs_body.get("json")
        if isinstance(raw, list):
            raw = raw[0]
        queue = json.loads(raw) if isinstance(raw, str) else prefs_body
        self.assertEqual(queue.get("max_active_downloads"), 8)
        self.assertEqual(queue.get("max_active_torrents"), 24)
        self.assertTrue(queue.get("dont_count_slow_torrents"))
        self.assertTrue(queue.get("queueing_enabled"))
        self.assertEqual(queue.get("slow_torrent_inactive_timer"), 180)
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
            [
                "plex-recently-added-scan",
                "plex-recently-added",
                "radarr-scan",
                "sonarr-scan",
            ],
        )
        self.assertEqual(self.state.qbit_removed, [])
        self.assertEqual({a["name"] for a in self.state.apps}, {"Sonarr", "Radarr"})
        for app in self.state.apps:
            fields = {f["name"]: f.get("value") for f in app.get("fields") or []}
            self.assertEqual(fields.get("prowlarrUrl"), "http://127.0.0.1:9698")
            if app["name"] == "Radarr":
                self.assertEqual(fields.get("syncCategories"), ws.RADARR_SYNC_CATS)
                self.assertNotIn("syncAnimeStandardFormatSearch", fields)
            else:
                self.assertEqual(fields.get("syncCategories"), ws.SONARR_SYNC_CATS)
                self.assertTrue(fields.get("syncAnimeStandardFormatSearch"))
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
        self.assertEqual(self.state.seerr_radarr[0]["activeDirectory"], "/media/Movies/By Rating")
        self.assertEqual(self.state.seerr_sonarr[0]["activeDirectory"], "/media/TV/By Rating")
        self.assertEqual(self.state.seerr_sonarr[0]["activeAnimeDirectory"], "/media/TV/By Rating")
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

    def test_prunes_stale_movie_and_tv_roots_from_old_defaults(self):
        """0.2.20 `/media` roots and a flattened Not Kid path leave the dropdown.

        Movies get the same cleanup as TV: By Rating + Kid + Not Kid only.
        """
        os.environ["MEDIA_ROOT"] = "/media/dlna"
        self.state.sonarr_folders = [
            "/media/TV",
            "/media/Kid Friendly TV",
            "/media/dlna/TV Not Kid Friendly",
            "/media/dlna/TV/Kid Friendly",
        ]
        self.state.radarr_folders = [
            "/media/Movies",
            "/media/Kid Friendly Movies",
            "/media/dlna/Movies Not Kid Friendly",
            "/media/dlna/Movies/Kid Friendly",
        ]
        self.state.series = [
            {"id": 40, "title": "Old Dump Show", "path": "/media/TV/Old Dump Show"},
            {
                "id": 41,
                "title": "Old Kid Show",
                "path": "/media/Kid Friendly TV/Old Kid Show",
            },
            {
                "id": 42,
                "title": "Flat General Show",
                "path": "/media/dlna/TV Not Kid Friendly/Flat General Show",
            },
            {
                "id": 43,
                "title": "Current Kid Show",
                "path": "/media/dlna/TV/Kid Friendly/Current Kid Show",
            },
        ]
        self.state.movies = [
            {"id": 50, "title": "Old Dump Movie", "path": "/media/Movies/Old Dump Movie"},
            {
                "id": 51,
                "title": "Old Kid Movie",
                "path": "/media/Kid Friendly Movies/Old Kid Movie",
            },
            {
                "id": 52,
                "title": "Flat General Movie",
                "path": "/media/dlna/Movies Not Kid Friendly/Flat General Movie",
            },
            {
                "id": 53,
                "title": "Current Kid Movie",
                "path": "/media/dlna/Movies/Kid Friendly/Current Kid Movie",
            },
        ]
        self.state.import_lists = [
            {"id": 7, "rootFolderPath": "/media/TV"},
            {"id": 8, "rootFolderPath": "/media/Movies"},
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(
            set(self.state.sonarr_folders),
            {
                "/media/dlna/TV/By Rating",
                "/media/dlna/TV/Not Kid Friendly",
                "/media/dlna/TV/Kid Friendly",
            },
        )
        self.assertEqual(
            set(self.state.radarr_folders),
            {
                "/media/dlna/Movies/By Rating",
                "/media/dlna/Movies/Not Kid Friendly",
                "/media/dlna/Movies/Kid Friendly",
            },
        )
        series = {item["title"]: item.get("rootFolderPath") or item.get("path") for item in self.state.series}
        movies = {item["title"]: item.get("rootFolderPath") or item.get("path") for item in self.state.movies}
        self.assertTrue(str(series["Old Dump Show"]).startswith("/media/dlna/TV/By Rating"))
        self.assertTrue(str(series["Old Kid Show"]).startswith("/media/dlna/TV/Kid Friendly"))
        self.assertTrue(
            str(series["Flat General Show"]).startswith("/media/dlna/TV/Not Kid Friendly")
        )
        self.assertTrue(
            str(series["Current Kid Show"]).startswith("/media/dlna/TV/Kid Friendly")
        )
        self.assertTrue(str(movies["Old Dump Movie"]).startswith("/media/dlna/Movies/By Rating"))
        self.assertTrue(
            str(movies["Old Kid Movie"]).startswith("/media/dlna/Movies/Kid Friendly")
        )
        self.assertTrue(
            str(movies["Flat General Movie"]).startswith("/media/dlna/Movies/Not Kid Friendly")
        )
        self.assertTrue(
            str(movies["Current Kid Movie"]).startswith("/media/dlna/Movies/Kid Friendly")
        )
        lists = {item["id"]: item.get("rootFolderPath") for item in self.state.import_lists}
        self.assertEqual(lists[7], "/media/dlna/TV/By Rating")
        self.assertEqual(lists[8], "/media/dlna/Movies/By Rating")

    def test_wire_applies_custom_simultaneous_downloads(self):
        os.environ["SIMULTANEOUS_DOWNLOADS"] = "12"
        rc = ws.main()
        self.assertEqual(rc, 0)
        raw = self.state.qbit_prefs.get("json")
        if isinstance(raw, list):
            raw = raw[0]
        queue = json.loads(raw)
        self.assertEqual(queue.get("max_active_downloads"), 12)
        self.assertEqual(queue.get("max_active_uploads"), 12)
        self.assertEqual(queue.get("max_active_torrents"), 28)
        self.assertTrue(queue.get("dont_count_slow_torrents"))

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
        # Stubs clone stock Arr items until Recyclarr writes TRaSH groups. CAM
        # on Default/Max is acceptable in that degraded window.
        _allowed, _blocked = allowed_quality_names(default)
        any_allowed, _any_blocked = allowed_quality_names(anything)
        self.assertIn("CAM", any_allowed)
        self.assertFalse(anything.get("upgradeAllowed"))
        cf_names = {item["name"] for item in self.state.radarr_formats}
        self.assertIn("Pompey Prefer x265", cf_names)
        self.assertIn("Pompey Prefer Remux", cf_names)
        self.assertIn("Pompey Dual Audio", cf_names)
        self.assertIn(ws.NOT_ORIGINAL_LANGUAGE, cf_names)
        self.assertNotIn("Pompey English dub", cf_names)
        self.assertNotIn("Pompey English subs", cf_names)
        scores = {item["name"]: item["score"] for item in default.get("formatItems") or []}
        self.assertEqual(scores.get("Pompey Prefer x265", 0), 0)
        self.assertEqual(scores[ws.NOT_ORIGINAL_LANGUAGE], -10000)
        self.assertEqual(scores.get(ws.DUAL_AUDIO), ws.DUAL_AUDIO_SCORE)
        self.assertEqual(default.get("language"), {"id": -1, "name": "Any"})
        max_scores = {item["name"]: item["score"] for item in maximum.get("formatItems") or []}
        self.assertEqual(max_scores.get("Pompey Prefer Remux", 0), 0)
        self.assertEqual(max_scores[ws.NOT_ORIGINAL_LANGUAGE], -10000)
        self.assertEqual(max_scores.get(ws.DUAL_AUDIO), ws.DUAL_AUDIO_SCORE)
        any_scores = {item["name"]: item["score"] for item in anything.get("formatItems") or []}
        self.assertEqual(any_scores.get("Pompey Prefer Proper"), 10)
        self.assertEqual(any_scores.get(ws.NOT_ORIGINAL_LANGUAGE, 0), 0)
        self.assertEqual(any_scores.get(ws.DUAL_AUDIO, 0), 0)
        cf_item_names = {item["name"] for item in default.get("formatItems") or []}
        self.assertEqual(cf_item_names, cf_names)
        self.assertGreaterEqual(default.get("minUpgradeFormatScore") or 0, 1)
        web = next(item for item in self.state.radarr_defs if item["quality"]["name"] == "WEBDL-1080p")
        self.assertEqual(web["minSize"], 0)
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

    def test_rehomes_titles_then_drops_leftover_profile(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        leftover = json.loads(json.dumps(any_quality_bundle(2, "HD-1080p")["profile"]))
        self.state.radarr_profiles.append(leftover)
        sonarr_leftover = json.loads(json.dumps(any_quality_bundle(2, "HD-1080p")["profile"]))
        self.state.sonarr_profiles.append(sonarr_leftover)
        self.state.movies[0]["qualityProfileId"] = 2
        self.state.series[0]["qualityProfileId"] = 2
        self.state.import_lists = [{"id": 9, "name": "TMDB", "qualityProfileId": 2}]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(
            {item.get("name") for item in self.state.radarr_profiles},
            {"Max", "Default", "Anything"},
        )
        self.assertEqual(
            {item.get("name") for item in self.state.sonarr_profiles},
            {"Max", "Default", "Anything"},
        )
        default_id = profile_named(self.state.radarr_profiles, "Default")["id"]
        self.assertEqual(self.state.movies[0]["qualityProfileId"], default_id)
        sonarr_default = profile_named(self.state.sonarr_profiles, "Default")["id"]
        self.assertEqual(self.state.series[0]["qualityProfileId"], sonarr_default)
        self.assertEqual(self.state.import_lists[0]["qualityProfileId"], default_id)
        self.assertNotEqual(default_id, 2)
        editor = [
            call
            for call in self.state.calls
            if call[0] == "radarr" and call[1] == "PUT" and str(call[2]).endswith("/movie/editor")
        ]
        self.assertTrue(editor)
        self.assertIn("qualityProfileId", editor[0][3] or {})

    def test_grants_advanced_requests_to_existing_seerr_user(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_users = [{"id": 2, "email": "house@local", "permissions": 32 + 128}]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue(self.state.seerr_users[0]["permissions"] & ws.SEERR_REQUEST_ADVANCED)
        user_puts = [
            call
            for call in self.state.calls
            if call[0] == "seerr" and call[1] == "PUT" and "/user/" in str(call[2])
        ]
        self.assertTrue(user_puts)
        self.assertNotIn("id", user_puts[0][3] or {})
        self.assertNotIn("email", user_puts[0][3] or {})
        self.assertEqual(
            set((user_puts[0][3] or {}).keys()),
            {"permissions"},
        )

    def test_seerr_permission_update_omits_readonly_email(self):
        user = {
            "id": 1,
            "email": "plex@local",
            "permissions": 32 + 128,
            "displayName": "Admin",
        }
        payload = ws.seerr_permission_update(user)
        self.assertEqual(payload, {"permissions": 32 + 128 + ws.SEERR_REQUEST_ADVANCED})
        self.assertIsNone(
            ws.seerr_permission_update(
                {"id": 1, "email": "plex@local", "permissions": ws.SEERR_REQUEST_ADVANCED}
            )
        )

    def test_not_original_language_cf_uses_arr_language_id(self):
        spec = ws.not_original_language_format()["specifications"][0]
        self.assertEqual(spec["implementation"], "LanguageSpecification")
        self.assertTrue(spec["negate"])
        self.assertEqual(spec["fields"][0]["value"], -2)
        default = ws.household_format_scores("Default")
        maximum = ws.household_format_scores("Max")
        anything = ws.household_format_scores("Anything")
        self.assertEqual(
            default,
            {ws.NOT_ORIGINAL_LANGUAGE: -10000, ws.DUAL_AUDIO: ws.DUAL_AUDIO_SCORE},
        )
        self.assertEqual(
            maximum,
            {ws.NOT_ORIGINAL_LANGUAGE: -10000, ws.DUAL_AUDIO: ws.DUAL_AUDIO_SCORE},
        )
        self.assertEqual(anything.get(ws.NOT_ORIGINAL_LANGUAGE, 0), 0)
        self.assertEqual(anything.get(ws.DUAL_AUDIO, 0), 0)
        self.assertEqual(anything.get("Pompey Prefer Proper"), 10)

    def test_recyclarr_yaml_names_default_max_and_keeps_1080p_fallback(self):
        body = recyclarr.render_config(
            "http://127.0.0.1:7878",
            "radarr-secret-key",
            "http://127.0.0.1:8989",
            "sonarr-secret-key",
        )
        self.assertIn(recyclarr.TRASH_RADARR_HD, body)
        self.assertIn(recyclarr.TRASH_RADARR_UHD, body)
        self.assertIn(recyclarr.TRASH_SONARR_HD, body)
        self.assertIn(recyclarr.TRASH_SONARR_UHD, body)
        self.assertIn(recyclarr.TRASH_RADARR_ANIME_DUAL_AUDIO, body)
        self.assertIn(recyclarr.TRASH_SONARR_ANIME_DUAL_AUDIO, body)
        self.assertIn(recyclarr.TRASH_SONARR_NOT_ORIGINAL, body)
        self.assertIn(f"score: {recyclarr.DUAL_AUDIO_SCORE}", body)
        self.assertIn("score: -10000", body)
        self.assertIn("name: Default", body)
        self.assertIn("name: Max", body)
        self.assertNotIn("name: Anything", body)
        self.assertIn("until_quality: Bluray-2160p", body)
        self.assertIn("until_quality: WEB 2160p", body)
        self.assertIn("- name: Bluray-1080p", body)
        self.assertIn("- name: WEB 1080p", body)
        self.assertIn("- name: Remux-2160p", body)
        self.assertIn("enabled: false", body)
        self.assertIn("delete_old_custom_formats: false", body)
        redacted = recyclarr.redact(body, {"radarr_api_key": "radarr-secret-key"})
        self.assertNotIn("radarr-secret-key", redacted)
        self.assertIn("***", redacted)

    def _radarr_default_puts(self, default_id: int) -> list:
        return [
            call
            for call in self.state.calls
            if call[0] == "radarr"
            and call[1] == "PUT"
            and str(call[2]) == f"/api/v3/qualityprofile/{default_id}"
        ]

    def test_recyclarr_success_skips_later_default_max_puts(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        fake = self.tmp / "recyclarr"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        os.environ["POMPEY_RECYCLARR"] = str(fake)
        os.environ["POMPEY_RECYCLARR_DATA"] = str(self.tmp / "recyclarr-data")
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "recyclarr").exists())
        yaml_path = self.tmp / "recyclarr-data" / "recyclarr.yml"
        self.assertTrue(yaml_path.is_file())
        self.assertEqual(oct(yaml_path.stat().st_mode & 0o777), "0o600")
        default_id = profile_named(self.state.radarr_profiles, "Default")["id"]
        self.assertEqual(self._radarr_default_puts(default_id), [])
        marker = self.ready / "recyclarr"
        os.utime(marker, (1, 1))
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(self._radarr_default_puts(default_id), [])

    def test_recyclarr_bin_uses_nested_launcher(self):
        engines = self.tmp / "engines-nested"
        (engines / "recyclarr").mkdir(parents=True)
        launcher = engines / "recyclarr" / "recyclarr"
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
        os.environ["POMPEY_ENGINES"] = str(engines)
        os.environ.pop("POMPEY_RECYCLARR", None)
        self.assertEqual(recyclarr.recyclarr_bin(), str(launcher))
        self.assertEqual(ws.recyclarr_binary(), str(launcher))

    def test_existing_default_items_are_not_rewritten(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        default = json.loads(json.dumps(any_quality_bundle(1, "Default")["profile"]))
        for item in default["items"]:
            q = item.get("quality") if isinstance(item.get("quality"), dict) else {}
            if q.get("name") == "CAM":
                item["allowed"] = False
        self.state.radarr_profiles = [default]
        rc = ws.main()
        self.assertEqual(rc, 0)
        after = profile_named(self.state.radarr_profiles, "Default")
        _allowed, blocked = allowed_quality_names(after)
        self.assertIn("CAM", blocked)
        self.assertEqual(self._radarr_default_puts(default["id"]), [])
        names = {item.get("name") for item in self.state.radarr_profiles}
        self.assertEqual(names, {"Max", "Default", "Anything"})

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
                "movie": {
                    "id": 1,
                    "title": "Matched",
                    "path": dest,
                    "hasFile": False,
                },
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
        self.assertEqual(paths, [str(wanted), str(blocked)])
        out = buf.getvalue()
        self.assertIn(
            f"importing remux.mkv into {dest} despite Not a wanted quality for Default "
            "(manual grab; Arr already has this title)",
            out,
        )
        self.assertIn(
            f"importing matched.mkv into {dest} (Arr title folder, not a guess from the filename)",
            out,
        )
        self.assertIn(
            "no library match for random-file.mkv (request it in search first so Arr "
            "has a Kid / Not Kid folder; leaving it in complete/)",
            out,
        )
        self.assertTrue(unknown.is_file())

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

    def test_housekeep_does_not_reimport_when_library_file_exists_without_hasfile(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "disk-hasfile"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S03E01.mkv"
        leftover.write_bytes(b"copy")
        os.environ["MEDIA_ROOT"] = str(root)
        tv = root / "TV" / "Not Kid Friendly" / "Silo"
        tv.mkdir(parents=True)
        (tv / "Silo.S03E01.mkv").write_bytes(b"library")
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(tv)}
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": False, "title": "Who Are You?"},
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
        self.assertEqual(ws.housekeep(), 0)
        manuals = [
            item for item in self.state.arr_commands if item.get("name") == "ManualImport"
        ]
        self.assertEqual(manuals, [])
        self.assertFalse(leftover.exists())
        self.assertTrue((tv / "Silo.S03E01.mkv").is_file())

    def test_housekeep_skips_import_when_complete_is_a_library_folder(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "complete-is-library"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        library = complete / "Silo.S03E01.mkv"
        library.write_bytes(b"library")
        os.environ["MEDIA_ROOT"] = str(root)
        os.environ["MEDIA_MOVIES"] = "downloads/complete"
        os.environ["MEDIA_TV"] = "downloads/complete"
        quality = {"quality": {"id": 4, "name": "WEBDL-1080p"}, "revision": {"version": 1}}
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(complete)}
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": True, "title": "Who Are You?"},
        ]
        self.state.sonarr_manual_import = [
            {
                "path": str(library),
                "seriesId": 10,
                "episodeIds": [11],
                "series": {"id": 10, "title": "Silo", "path": str(complete)},
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
        self.assertTrue(library.is_file())
        self.assertFalse(
            any(item.get("name") == "ManualImport" for item in self.state.arr_commands)
        )
        self.assertFalse(
            any("Scan" in str(item.get("name")) for item in self.state.arr_commands)
        )
        self.assertIn("overlaps a library folder", buf.getvalue())

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
            f"importing Show.S01E02.mkv into {dest} (Arr title folder, not a guess from the filename)",
            buf.getvalue(),
        )

    def test_housekeep_imports_prowlarr_grab_from_downloads_manual(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "manual-import"
        manual = root / "downloads" / "manual"
        manual.mkdir(parents=True)
        wanted = manual / "Show.S01E02.mkv"
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
                "rejections": [{"reason": "Not a wanted quality for Default"}],
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
            f"importing Show.S01E02.mkv into {dest} despite Not a wanted quality for Default "
            "(manual grab; Arr already has this title)",
            buf.getvalue(),
        )
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
        self.assertIn(str(manual), movie_paths)
        self.assertIn(str(manual), episode_paths)

    def test_housekeep_imports_manual_grab_onto_unmonitored_empty_folder(self):
        """Deleted-on-disk leftover Arr row: Prowlarr Grab still lands in that folder."""
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "ghost-row"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        grab = complete / "Show.S01E01.mkv"
        grab.write_bytes(b"human")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Show (2024)"
        dest.mkdir(parents=True)
        quality = {"quality": {"id": 4, "name": "HDTV-720p"}, "revision": {"version": 1}}
        self.state.series = [
            {
                "id": 10,
                "title": "Show",
                "monitored": False,
                "path": str(dest),
                "statistics": {"episodeFileCount": 1, "episodeCount": 1},
            }
        ]
        self.state.episodes = [
            {"id": 11, "seriesId": 10, "hasFile": True, "title": "Pilot"},
        ]
        self.state.sonarr_manual_import = [
            {
                "path": str(grab),
                "seriesId": 10,
                "episodeIds": [11],
                "series": {"id": 10, "title": "Show", "path": str(dest)},
                "quality": quality,
                "languages": [{"id": 1, "name": "English"}],
                "rejections": [{"reason": "Not a wanted quality for Default"}],
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
        self.assertEqual(
            [row.get("path") for row in manuals[0].get("files") or []],
            [str(grab)],
        )
        self.assertTrue(grab.is_file())
        out = buf.getvalue()
        self.assertIn("does not have this video; importing leftover", out)
        self.assertIn(
            f"importing Show.S01E01.mkv into {dest} despite Not a wanted quality for Default "
            "(manual grab; Arr already has this title)",
            out,
        )

    def test_housekeep_skips_season_search_while_matching_grab_sits_in_complete(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "pending-grab"
        manual = root / "downloads" / "manual"
        manual.mkdir(parents=True)
        grab = manual / "Silo.S02E01.mkv"
        grab.write_bytes(b"human")
        show = root / "TV" / "Not Kid Friendly" / "Silo"
        show.mkdir(parents=True)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(show),
                "statistics": {"episodeCount": 20, "episodeFileCount": 0},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "Silo"},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(
            any(
                item.get("name") in {"SeasonSearch", "EpisodeSearch"}
                for item in self.state.arr_commands
            ),
            self.state.arr_commands,
        )
        self.assertTrue(grab.is_file())
        self.assertIn(
            "not searching Silo; a matching file is still in complete/ or manual/ "
            "(manual grab, not a new SeasonSearch)",
            buf.getvalue(),
        )

    def test_housekeep_still_searches_when_complete_file_is_a_different_title(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "other-complete"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        (complete / "Unrelated.Movie.2024.mkv").write_bytes(b"x" * 40)
        show = root / "TV" / "Not Kid Friendly" / "Silo"
        show.mkdir(parents=True)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(show),
                "statistics": {"episodeCount": 20, "episodeFileCount": 0},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "Silo"},
            },
        ]
        self.assertEqual(ws.housekeep(), 0)
        seasons = [
            item for item in self.state.arr_commands if item.get("name") == "SeasonSearch"
        ]
        self.assertEqual(len(seasons), 1, self.state.arr_commands)
        self.assertEqual(seasons[0].get("seriesId"), 10)
        self.assertEqual(seasons[0].get("seasonNumber"), 2)

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
        self.assertIn("removed 1 leftover complete/manual folder(s)", out)
        self.assertFalse(folder.exists())
        self.assertNotIn("still in complete/:", out)
        self.assertNotIn("English.srt", out)

    def test_housekeep_places_tv_extras_and_drops_samples(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "plex-extras"
        release = root / "downloads" / "complete" / "Silo.S03.1080p"
        extras = release / "Extras"
        extras.mkdir(parents=True)
        (extras / "Making Of.mkv").write_bytes(b"extra")
        (extras / "Making Of.srt").write_bytes(b"sub")
        (release / "Silo.S03.Behind.The.Scenes.mkv").write_bytes(b"bts")
        (release / "Sample").mkdir()
        (release / "Sample" / "silo.sample.mkv").write_bytes(b"sample")
        (release / "release.nfo").write_bytes(b"nfo")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        season = dest / "Season 03"
        season.mkdir(parents=True)
        (season / "Silo - S03E01.mkv").write_bytes(b"library")
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(dest),
                "statistics": {"episodeFileCount": 1, "episodeCount": 1},
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertTrue((dest / "Other" / "Making Of.mkv").is_file())
        self.assertTrue((dest / "Other" / "Making Of.srt").is_file())
        self.assertTrue((season / "Behind The Scenes" / "Behind The Scenes.mkv").is_file())
        self.assertFalse(release.exists())
        self.assertFalse((dest / "Sample").exists())
        self.assertIn("placed 2 leftover extra/special video(s)", buf.getvalue())

    def test_housekeep_places_extras_specials_and_special_season(self):
        """One season pack: show extras, Season 03 extras, S00 specials, Season 00 extras."""
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "plex-bonus-pack"
        release = root / "downloads" / "complete" / "Silo.S03.COMPLETE.1080p"
        featurettes = release / "Featurettes"
        season00_src = release / "Season 00"
        featurettes.mkdir(parents=True)
        season00_src.mkdir(parents=True)
        (featurettes / "The Look.mkv").write_bytes(b"look")
        (release / "Silo.S03.Behind.The.Scenes.mkv").write_bytes(b"bts")
        (season00_src / "Silo.S00E01.Christmas.Special.mkv").write_bytes(b"xmas")
        (season00_src / "Cast Interview.mkv").write_bytes(b"cast")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        season03 = dest / "Season 03"
        season00 = dest / "Season 00"
        season03.mkdir(parents=True)
        season00.mkdir(parents=True)
        (season03 / "Silo - S03E01.mkv").write_bytes(b"library")
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(dest),
                "statistics": {"episodeFileCount": 1, "episodeCount": 1},
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertTrue((dest / "Featurettes" / "The Look.mkv").is_file())
        self.assertTrue((season03 / "Behind The Scenes" / "Behind The Scenes.mkv").is_file())
        self.assertTrue((season00 / "Silo - S00E01 - Christmas Special.mkv").is_file())
        self.assertTrue((season00 / "Interviews" / "Cast Interview.mkv").is_file())
        self.assertFalse((dest / "Specials").exists())
        self.assertFalse(release.exists())
        self.assertIn("placed 4 leftover extra/special video(s)", buf.getvalue())

    def test_housekeep_places_season_zero_specials(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "plex-specials"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S00E01.Christmas.Special.1080p.WEBRip.mkv"
        leftover.write_bytes(b"special")
        unnamed = complete / "Silo.Holiday.Special.mkv"
        unnamed.write_bytes(b"holiday")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        dest.mkdir(parents=True)
        (dest / "Silo.S03E01.mkv").write_bytes(b"library")
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(dest)}
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        specials = dest / "Specials"
        self.assertTrue((specials / "Silo - S00E01 - Christmas Special.mkv").is_file())
        self.assertTrue((specials / "Silo - S00E02 - Holiday Special.mkv").is_file())
        self.assertFalse(leftover.exists())
        self.assertFalse(unnamed.exists())
        self.assertIn("placed 2 leftover extra/special video(s)", buf.getvalue())

    def test_housekeep_reuses_existing_season_00_for_specials(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "season-00"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S00E03.Gag.Reel.mkv"
        leftover.write_bytes(b"gag")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        season00 = dest / "Season 00"
        season00.mkdir(parents=True)
        (season00 / "Silo - S00E01 - Pilot Special.mkv").write_bytes(b"old")
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(dest)}
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue((season00 / "Silo - S00E03 - Gag Reel.mkv").is_file())
        self.assertFalse((dest / "Specials").exists())
        self.assertFalse(leftover.exists())

    def test_housekeep_leaves_wanted_special_for_sonarr(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "wanted-special"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S00E01.Christmas.Special.mkv"
        leftover.write_bytes(b"special")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        dest.mkdir(parents=True)
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(dest)}
        ]
        self.state.episodes = [
            {
                "id": 21,
                "seriesId": 10,
                "seasonNumber": 0,
                "episodeNumber": 1,
                "hasFile": False,
                "title": "Christmas Special",
            }
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue(leftover.is_file())
        self.assertFalse((dest / "Specials").exists())

    def test_housekeep_does_not_steal_waiting_episode(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "waiting-ep"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S01E02.mkv"
        leftover.write_bytes(b"episode")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        dest.mkdir(parents=True)
        (dest / "Silo.S01E01.mkv").write_bytes(b"library")
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(dest)}
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue(leftover.is_file())
        self.assertFalse((dest / "Specials").exists())

    def test_housekeep_places_episode_tied_extra_next_to_episode(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "episode-extra"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "Silo.S01E01-behindthescenes.mkv"
        leftover.write_bytes(b"bts")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "TV" / "Not Kid Friendly" / "Silo"
        season = dest / "Season 01"
        season.mkdir(parents=True)
        episode = season / "Silo - S01E01 - Pilot.mkv"
        episode.write_bytes(b"library")
        self.state.series = [
            {"id": 10, "title": "Silo", "monitored": True, "path": str(dest)}
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue((season / "Silo - S01E01 - Pilot-behindthescenes.mkv").is_file())
        self.assertFalse(leftover.exists())

    def test_housekeep_does_not_guess_extras_folder_without_arr_title(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "no-guess"
        complete = root / "downloads" / "complete"
        complete.mkdir(parents=True)
        leftover = complete / "UnknownShow.S01.Behind.The.Scenes.mkv"
        leftover.write_bytes(b"bts")
        os.environ["MEDIA_ROOT"] = str(root)
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue(leftover.is_file())

    def test_housekeep_places_movie_featurette(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "movie-extra"
        release = root / "downloads" / "complete" / "Wake Up Dead Man 2025"
        featurettes = release / "Featurettes"
        featurettes.mkdir(parents=True)
        (featurettes / "Making Of.mkv").write_bytes(b"extra")
        os.environ["MEDIA_ROOT"] = str(root)
        dest = root / "Movies" / "Not Kid Friendly" / "Wake Up Dead Man (2025)"
        dest.mkdir(parents=True)
        (dest / "Wake Up Dead Man (2025).mkv").write_bytes(b"library")
        for movie in self.state.movies:
            if movie.get("id") == 2:
                movie["title"] = "Wake Up Dead Man"
                movie["hasFile"] = True
                movie["path"] = str(dest)
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue((dest / "Featurettes" / "Making Of.mkv").is_file())
        self.assertFalse(release.exists())

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
            [
                "plex-recently-added-scan",
                "plex-recently-added",
                "radarr-scan",
                "sonarr-scan",
            ],
        )

    def test_seerr_tickle_falls_back_when_job_name_404s(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.seerr_missing_jobs = {"plex-recently-added-scan"}
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(
            self.state.seerr_jobs,
            ["plex-recently-added", "radarr-scan", "sonarr-scan"],
        )

    def test_arr_api_root_prefers_v3(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = ws.arr_api_root(os.environ["RADARR_URL"], "radarr-key")
        self.assertTrue(root.endswith("/api/v3"))

    def test_arr_api_root_uses_v4_when_v3_is_gone(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.arr_drop_v3 = True
        root = ws.arr_api_root(os.environ["RADARR_URL"], "radarr-key")
        self.assertTrue(root.endswith("/api/v4"))
        rc = ws.main()
        self.assertEqual(rc, 0)
        v4 = [
            path
            for role, method, path, _body in self.state.calls
            if role == "radarr" and "/api/v4/" in str(path)
        ]
        self.assertTrue(v4, self.state.calls[-8:])
        v3 = [
            path
            for role, method, path, _body in self.state.calls
            if role == "radarr" and "/api/v3/" in str(path) and "qualityprofile" not in str(path)
        ]
        self.assertEqual(v3, [])

    def test_housekeep_tries_download_scan_alias(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        os.environ["MEDIA_ROOT"] = str(self.tmp)
        complete = self.tmp / "downloads" / "complete"
        complete.mkdir(parents=True, exist_ok=True)
        (complete / "stuck-title.mkv").write_bytes(b"x" * 50)
        self.state.reject_command_names = {"DownloadedMoviesScan", "DownloadedEpisodesScan"}
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        names = {item.get("name") for item in self.state.arr_commands}
        self.assertIn("DownloadedMovieScan", names)
        self.assertIn("DownloadedEpisodeScan", names)
        self.assertNotIn("DownloadedMoviesScan", names)

    def test_housekeep_skips_engine_refresh_without_checked_marker(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        called = self.tmp / "fetch-called"
        fake = self.tmp / "fake-fetch-engines"
        fake.write_text(f"#!/bin/sh\necho ran >{called}\nexit 0\n")
        fake.chmod(0o755)
        os.environ["POMPEY_FETCH_ENGINES"] = str(fake)
        self.assertFalse(ws.engines_refresh_due())
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertFalse(called.exists())

    def test_housekeep_refreshes_stale_engines_and_holds_qbit(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        marker = self.ready / "engines-checked"
        marker.write_text("1\n")
        os.utime(marker, (0, 0))
        hold = self.tmp / "fetch-env"
        fake = self.tmp / "fake-fetch-engines"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf 'HOLD=%s\\n' \"${{POMPEY_HOLD_QBIT:-}}\" >{hold}\n"
            f": >{self.ready / 'engines-changed'}\n"
            "exit 0\n"
        )
        fake.chmod(0o755)
        os.environ["POMPEY_FETCH_ENGINES"] = str(fake)
        os.environ["POMPEY_ENGINE_REFRESH_AGE"] = "1"
        self.state.qbit_torrents = [
            {
                "hash": "aa" * 20,
                "name": "writing",
                "state": "downloading",
                "progress": 0.4,
                "amount_left": 100,
                "content_path": str(self.tmp / "incomplete.bin"),
            }
        ]
        self.assertTrue(ws.engines_refresh_due())
        self.assertTrue(ws.qbit_has_active_transfers())
        rc = ws.housekeep()
        self.assertEqual(rc, 0)
        self.assertEqual(hold.read_text().strip(), "HOLD=1")

    def test_qbit_has_active_transfers_ignores_finished_seed(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.qbit_torrents = [
            {
                "hash": "bb" * 20,
                "name": "done",
                "state": "uploading",
                "progress": 1,
                "amount_left": 0,
                "content_path": str(self.tmp / "done.mkv"),
            }
        ]
        self.assertFalse(ws.qbit_has_active_transfers())

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

    def test_rewrites_prowlarr_qbit_off_the_radarr_category(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.prowlarr_clients = [
            {
                "id": 3,
                "name": "qBittorrent",
                "implementation": "QBittorrent",
                "enable": True,
                "removeCompletedDownloads": True,
                "removeFailedDownloads": True,
                "fields": [
                    {"name": "host", "value": "127.0.0.1"},
                    {"name": "port", "value": 8080},
                    {"name": "category", "value": "radarr"},
                    {"name": "movieCategory", "value": "radarr"},
                    {"name": "tvCategory", "value": "sonarr"},
                ],
            }
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.state.prowlarr_clients), 1)
        client = self.state.prowlarr_clients[0]
        self.assertEqual(client["id"], 3)
        self.assertFalse(client.get("removeCompletedDownloads"))
        self.assertTrue(client.get("removeFailedDownloads"))
        fields = {f["name"]: f.get("value") for f in client.get("fields") or []}
        self.assertEqual(fields.get("category"), "prowlarr")
        self.assertEqual(fields.get("movieCategory"), "prowlarr")
        self.assertEqual(fields.get("tvCategory"), "prowlarr")
        self.assertEqual(len(self.state.download_clients), 2)
        for arr_client in self.state.download_clients:
            arr_fields = {f["name"]: f.get("value") for f in arr_client.get("fields") or []}
            self.assertIn(arr_fields.get("movieCategory") or arr_fields.get("tvCategory"), {"radarr", "sonarr"})

    def test_turns_on_arr_interactive_search_flags(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        flags = {
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": True,
        }
        self.state.indexers = [
            {"id": 1, "name": "Tracker A", "enable": True, **flags},
        ]
        self.state.radarr_indexers = [
            {
                "id": 1,
                "name": "Tracker A",
                "enable": True,
                "enableRss": True,
                "enableAutomaticSearch": True,
                "enableInteractiveSearch": False,
            }
        ]
        self.state.sonarr_indexers = [
            {"id": 1, "name": "Tracker A", "enable": True},
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue(self.state.radarr_indexers[0]["enableInteractiveSearch"])
        self.assertTrue(self.state.sonarr_indexers[0]["enableRss"])
        self.assertTrue(self.state.sonarr_indexers[0]["enableAutomaticSearch"])
        self.assertTrue(self.state.sonarr_indexers[0]["enableInteractiveSearch"])
        sonarr_fields = {
            f["name"]: f.get("value")
            for f in self.state.sonarr_indexers[0].get("fields") or []
        }
        self.assertTrue(sonarr_fields.get("animeStandardFormatSearch"))
        radarr_puts = [
            call
            for call in self.state.calls
            if call[0] == "radarr" and call[1] == "PUT" and str(call[2]).endswith("/indexer/1")
        ]
        sonarr_puts = [
            call
            for call in self.state.calls
            if call[0] == "sonarr" and call[1] == "PUT" and str(call[2]).endswith("/indexer/1")
        ]
        self.assertTrue(radarr_puts)
        self.assertTrue(sonarr_puts)

    def test_turns_on_sonarr_anime_standard_format_search(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        flags = {
            "enableRss": True,
            "enableAutomaticSearch": True,
            "enableInteractiveSearch": True,
        }
        self.state.sonarr_indexers = [
            {
                "id": 1,
                "name": "Nyaa.si",
                "enable": True,
                **flags,
                "fields": [{"name": "animeStandardFormatSearch", "value": False}],
            }
        ]
        self.state.apps = [
            {
                "id": 3,
                "name": "Sonarr",
                "fields": [
                    {"name": "prowlarrUrl", "value": "http://127.0.0.1:9698"},
                    {"name": "baseUrl", "value": "http://127.0.0.1:8989"},
                    {"name": "apiKey", "value": "sonarr-key"},
                    {"name": "syncCategories", "value": list(ws.SONARR_SYNC_CATS)},
                    {"name": "syncAnimeStandardFormatSearch", "value": False},
                ],
            }
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        sonarr_app = next(item for item in self.state.apps if item["name"] == "Sonarr")
        app_fields = {f["name"]: f.get("value") for f in sonarr_app.get("fields") or []}
        self.assertTrue(app_fields.get("syncAnimeStandardFormatSearch"))
        idx_fields = {
            f["name"]: f.get("value")
            for f in self.state.sonarr_indexers[0].get("fields") or []
        }
        self.assertTrue(idx_fields.get("animeStandardFormatSearch"))

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
            self.assertEqual(cfg.get("recycleBin"), "/media/downloads/recycle")
            self.assertEqual(cfg.get("recycleBinCleanupDays"), 0)
            self.assertFalse(cfg.get("deleteEmptyFolders"))
            self.assertFalse(cfg.get("useScriptImport"))
        self.assertFalse(
            self.state.radarr_media.get("autoUnmonitorPreviouslyDownloadedMovies")
        )
        self.assertFalse(
            self.state.sonarr_media.get("autoUnmonitorPreviouslyDownloadedEpisodes")
        )
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

    def test_dual_audio_regex_matches_release_tags(self):
        import re

        pattern = next(rx for name, rx in ws.CUSTOM_FORMATS if name == ws.DUAL_AUDIO)
        rx = re.compile(pattern, re.I)
        self.assertTrue(rx.search("Show.S01E01.Dual-Audio.1080p"))
        self.assertTrue(rx.search("Show.S01E01.Dual.Audio.1080p"))
        self.assertTrue(rx.search("[SubsPlease] Title - 01 (1080p) [DUAL]"))
        self.assertTrue(rx.search("Title.JA+EN.WEB"))
        self.assertTrue(rx.search("Title.Multi-Audio.Bluray"))
        self.assertTrue(rx.search("Show.S01.DUAL.1080p"))
        self.assertFalse(rx.search("Title.1080p.WEB-DL.x265"))
        self.assertFalse(rx.search("Show.S01.Dual.Subs.1080p"))
        self.assertFalse(rx.search("Show.S01.Dual Subs.1080p"))
        self.assertFalse(rx.search("Show.S01-S03.1080p.Dual.Subs-GROUP"))
        self.assertFalse(rx.search("[Group] Show - S01-S03 (1080p) [Dual Subs]"))
        self.assertFalse(rx.search("Show Dual Subtitles 1080p"))
        subs = re.compile(ws.DUAL_SUBS_RE, re.I)
        self.assertTrue(subs.search("Show.S01-S03.1080p.Dual.Subs-GROUP"))
        self.assertTrue(subs.search("[Group] Show (1080p) [Dual Subs]"))
        self.assertTrue(subs.search("Show Dual Subtitles 1080p"))
        self.assertFalse(subs.search("Show.S01.Dual-Audio.1080p"))
        self.assertFalse(subs.search("[SubsPlease] Title - 01 (1080p) [DUAL]"))

    def test_pompey_dual_audio_format_requires_not_dual_subs(self):
        payload = next(
            item for item in ws.household_custom_formats() if item.get("name") == ws.DUAL_AUDIO
        )
        self.assertTrue(ws.format_excludes_dual_subs(payload))

    def test_housekeep_adds_dual_subs_negate_to_trash_dual_audio(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        trash = {
            "id": 40,
            "name": "Anime Dual Audio",
            "includeCustomFormatWhenRenaming": False,
            "specifications": [
                {
                    "name": "Dual Audio",
                    "implementation": "ReleaseTitleSpecification",
                    "negate": False,
                    "required": True,
                    "fields": [{"name": "value", "value": r"dual[ ._-]?(audio)|\\bDUAL\\b"}],
                }
            ],
        }
        self.state.sonarr_formats = [json.loads(json.dumps(trash))]
        self.state.radarr_formats = [json.loads(json.dumps(trash))]
        self.assertEqual(ws.housekeep(), 0)
        sonarr = next(
            item for item in self.state.sonarr_formats if item.get("name") == "Anime Dual Audio"
        )
        radarr = next(
            item for item in self.state.radarr_formats if item.get("name") == "Anime Dual Audio"
        )
        self.assertTrue(ws.format_excludes_dual_subs(sonarr))
        self.assertTrue(ws.format_excludes_dual_subs(radarr))

    def test_cutoff_unmet_searches_open_seerr_requests_when_sources_change(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.indexers = [{"id": 1, "name": "Old", "enable": True}]
        self.state.seerr_requests = [
            {
                "id": 7,
                "status": 5,
                "media": {
                    "mediaType": "movie",
                    "tmdbId": 111,
                    "externalServiceId": 99,
                },
            }
        ]
        self.state.radarr_wanted_cutoff = [
            {"id": 99, "title": "Open Anime", "tmdbId": 111, "hasFile": True},
            {"id": 100, "title": "Closed Movie", "tmdbId": 222, "hasFile": True},
        ]
        self.state.sonarr_wanted_cutoff = [
            {
                "id": 55,
                "seriesId": 10,
                "series": {"id": 10, "title": "Show", "tvdbId": 88},
            }
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        first = StringIO()
        with redirect_stdout(first):
            self.assertEqual(ws.housekeep(), 0)
        def movie_search(item: dict) -> bool:
            name = str(item.get("name") or "")
            return name.startswith("Movies") and name.endswith("Search")

        movie_searches = [
            item
            for item in self.state.arr_commands
            if movie_search(item) and item.get("movieIds") == [99]
        ]
        self.assertEqual(len(movie_searches), 1, self.state.arr_commands)
        self.assertFalse(
            any(
                movie_search(item) and 100 in (item.get("movieIds") or [])
                for item in self.state.arr_commands
            )
        )
        self.assertFalse(
            any(
                item.get("name") == "EpisodeSearch" and item.get("episodeIds") == [55]
                for item in self.state.arr_commands
            )
        )
        self.assertIn("better copy of 1 movie(s) still requested in Seerr", first.getvalue())

        self.state.arr_commands.clear()
        second = StringIO()
        with redirect_stdout(second):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(any(movie_search(item) for item in self.state.arr_commands))
        self.assertNotIn("better copy", second.getvalue())

        self.state.indexers.append({"id": 2, "name": "New", "enable": True})
        third = StringIO()
        with redirect_stdout(third):
            self.assertEqual(ws.housekeep(), 0)
        later = [
            item
            for item in self.state.arr_commands
            if movie_search(item) and item.get("movieIds") == [99]
        ]
        self.assertEqual(len(later), 1)
        self.assertIn("better copy of 1 movie(s) still requested in Seerr", third.getvalue())

    def test_cutoff_unmet_season_searches_open_tv_request_once(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.indexers = [{"id": 1, "name": "Nyaa.si", "enable": True}]
        self.state.seerr_requests = [
            {
                "id": 8,
                "status": 5,
                "media": {
                    "mediaType": "tv",
                    "tvdbId": 88,
                    "externalServiceId": 10,
                },
            }
        ]
        self.state.sonarr_wanted_cutoff = [
            {
                "id": 55,
                "seriesId": 10,
                "seasonNumber": 1,
                "series": {"id": 10, "title": "World Trigger", "tvdbId": 88},
            },
            {
                "id": 56,
                "seriesId": 10,
                "seasonNumber": 1,
                "series": {"id": 10, "title": "World Trigger", "tvdbId": 88},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        first = StringIO()
        with redirect_stdout(first):
            self.assertEqual(ws.housekeep(), 0)
        seasons = [
            item for item in self.state.arr_commands if item.get("name") == "SeasonSearch"
        ]
        self.assertEqual(len(seasons), 1, self.state.arr_commands)
        self.assertEqual(seasons[0].get("seriesId"), 10)
        self.assertEqual(seasons[0].get("seasonNumber"), 1)
        self.assertFalse(
            any(item.get("name") == "EpisodeSearch" for item in self.state.arr_commands)
        )
        self.assertIn("better copy of 1 season(s) still requested in Seerr", first.getvalue())

        self.state.arr_commands.clear()
        second = StringIO()
        with redirect_stdout(second):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(
            any(item.get("name") == "SeasonSearch" for item in self.state.arr_commands)
        )
        self.assertNotIn("better copy", second.getvalue())

    def test_unmonitor_after_seerr_request_removed(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.movies = [
            {"id": 99, "title": "Open Anime", "monitored": True, "hasFile": True},
            {"id": 1, "title": "Never Requested", "monitored": True, "hasFile": True},
        ]
        self.state.seerr_requests = [
            {
                "id": 7,
                "status": 5,
                "media": {"mediaType": "movie", "externalServiceId": 99},
            }
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue(self.state.movies[0]["monitored"])
        self.assertFalse(self.state.movies[1]["monitored"])

        self.state.seerr_requests = []
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(self.state.movies[0]["monitored"])
        self.assertFalse(self.state.movies[1]["monitored"])
        self.assertIn("stopped looking for a better copy of Open Anime", buf.getvalue())

    def test_closeout_unmonitors_without_remembered_upgrade_ids(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.seerr_requests = []
        self.state.movies = [
            {"id": 99, "title": "Arr Only", "monitored": True, "hasFile": True, "tmdbId": 111},
        ]
        self.assertEqual(ws.closeout(), 0)
        self.assertFalse(self.state.movies[0]["monitored"])

    def test_removed_request_does_not_season_search_same_cycle(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.seerr_requests = []
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "monitorNewItems": "all",
                "path": "/media/TV/Not Kid Friendly/Silo",
                "seasons": [
                    {"seasonNumber": 1, "monitored": True},
                    {"seasonNumber": 2, "monitored": True},
                ],
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "Silo"},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(self.state.series[0]["monitored"])
        self.assertEqual(self.state.series[0].get("monitorNewItems"), "none")
        self.assertFalse(any(season.get("monitored") for season in self.state.series[0]["seasons"]))
        self.assertFalse(
            any(
                item.get("name") in {"SeasonSearch", "EpisodeSearch", "SeriesSearch"}
                for item in self.state.arr_commands
            ),
            self.state.arr_commands,
        )
        self.assertIn("stopped looking for a better copy of Silo", buf.getvalue())

    def test_closeout_unmonitors_seasons_and_cancels_inflight_search(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.seerr_requests = []
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": False,
                "monitorNewItems": "all",
                "seasons": [
                    {"seasonNumber": 1, "monitored": False},
                    {"seasonNumber": 2, "monitored": True},
                ],
            }
        ]
        self.state.sonarr_command_queue = [
            {
                "id": 44,
                "name": "SeasonSearch",
                "status": "started",
                "body": {"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 2},
            },
            {
                "id": 45,
                "name": "RefreshSeries",
                "status": "queued",
                "body": {"name": "RefreshSeries", "seriesId": 10},
            },
        ]
        self.state.sonarr_queue = [
            {"id": 7, "seriesId": 10, "title": "Silo.S02E01"},
            {"id": 8, "seriesId": 99, "title": "Other"},
        ]
        self.assertEqual(ws.closeout(), 0)
        show = self.state.series[0]
        self.assertFalse(show["monitored"])
        self.assertEqual(show.get("monitorNewItems"), "none")
        self.assertFalse(any(season.get("monitored") for season in show["seasons"]))
        self.assertEqual(self.state.cancelled_commands, [{"role": "sonarr", "id": 44}])
        self.assertEqual(
            [item["id"] for item in self.state.sonarr_command_queue],
            [45],
        )
        self.assertEqual(
            self.state.aborted_queue,
            [
                {
                    "role": "sonarr",
                    "id": 7,
                    "removeFromClient": "true",
                    "blocklist": "false",
                    "skipRedownload": "true",
                }
            ],
        )
        self.assertEqual([item["id"] for item in self.state.sonarr_queue], [8])
        self.assertFalse(
            any(
                item.get("name") in {"SeasonSearch", "EpisodeSearch"}
                for item in self.state.arr_commands
            )
        )

    def test_closeout_keeps_open_seerr_request_hunting(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.movies = [
            {"id": 99, "title": "Open Anime", "monitored": True, "hasFile": False, "tmdbId": 111},
        ]
        self.state.seerr_requests = [
            {
                "id": 7,
                "status": 5,
                "media": {"mediaType": "movie", "tmdbId": 111, "externalServiceId": 99},
            }
        ]
        self.state.radarr_command_queue = [
            {
                "id": 3,
                "name": "MoviesSearch",
                "status": "started",
                "body": {"name": "MoviesSearch", "movieIds": [99]},
            }
        ]
        self.state.queue = [{"id": 1, "movieId": 99, "title": "Open Anime"}]
        self.assertEqual(ws.closeout(), 0)
        self.assertTrue(self.state.movies[0]["monitored"])
        self.assertEqual(self.state.cancelled_commands, [])
        self.assertEqual(self.state.aborted_queue, [])
        self.assertEqual(len(self.state.radarr_command_queue), 1)
        self.assertEqual(len(self.state.queue), 1)

    def test_seerr_request_read_failure_does_not_unmonitor(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.seerr_has_admin = True
        self.state.movies = [
            {"id": 99, "title": "Open Anime", "monitored": True, "hasFile": True},
        ]
        self.state.seerr_requests = [
            {
                "id": 7,
                "status": 5,
                "media": {"mediaType": "movie", "externalServiceId": 99},
            }
        ]
        self.assertEqual(ws.housekeep(), 0)
        self.state.seerr_requests = []
        self.state.seerr_fail_requests = True
        self.assertEqual(ws.housekeep(), 0)
        self.assertTrue(self.state.movies[0]["monitored"])

    def test_housekeep_retries_missing_episodes_once_per_id_set(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 3,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 3,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 3,
                "seriesId": 10,
                "seasonNumber": 3,
                "episodeNumber": 3,
                "series": {"id": 10, "title": "Silo"},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        first = StringIO()
        with redirect_stdout(first):
            self.assertEqual(ws.housekeep(), 0)
        searches = [
            item for item in self.state.arr_commands if item.get("name") == "SeasonSearch"
        ]
        self.assertEqual(len(searches), 1, self.state.arr_commands)
        self.assertEqual(searches[0].get("seriesId"), 10)
        self.assertEqual(searches[0].get("seasonNumber"), 3)
        self.assertFalse(
            any(item.get("name") == "EpisodeSearch" for item in self.state.arr_commands)
        )
        self.assertIn("searching again for 1 missing season(s)", first.getvalue())
        self.assertFalse(
            any(item.get("name") == "RefreshMonitoredDownloads" for item in self.state.arr_commands)
        )

        self.state.arr_commands.clear()
        second = StringIO()
        with redirect_stdout(second):
            self.assertEqual(ws.housekeep(), 0)
        self.assertEqual(
            [item for item in self.state.arr_commands if item.get("name") == "SeasonSearch"],
            [],
        )
        self.assertNotIn("searching again for", second.getvalue())

        # Same season still missing two holes is the same token — do not
        # episode-walk it again.
        self.state.wanted_missing = self.state.wanted_missing[:2]
        third = StringIO()
        with redirect_stdout(third):
            self.assertEqual(ws.housekeep(), 0)
        self.assertEqual(
            [
                item
                for item in self.state.arr_commands
                if item.get("name") in {"SeasonSearch", "EpisodeSearch"}
            ],
            [],
        )
        self.assertNotIn("searching again for", third.getvalue())

        self.state.wanted_missing = [
            {
                "id": 4,
                "seriesId": 10,
                "seasonNumber": 4,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            }
        ]
        fourth = StringIO()
        with redirect_stdout(fourth):
            self.assertEqual(ws.housekeep(), 0)
        singles = [
            item for item in self.state.arr_commands if item.get("name") == "EpisodeSearch"
        ]
        self.assertEqual(len(singles), 1)
        self.assertEqual(singles[0].get("episodeIds"), [4])
        self.assertIn("searching again for 1 missing episode(s)", fourth.getvalue())

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
            "/media/dlna/Movies/By Rating",
        )
        self.assertEqual(
            self.state.seerr_sonarr[0]["activeDirectory"],
            "/media/dlna/TV/By Rating",
        )
        self.assertEqual(
            self.state.seerr_sonarr[0]["activeAnimeDirectory"],
            "/media/dlna/TV/By Rating",
        )
        self.assertEqual(self.state.seerr_radarr[0]["activeProfileName"], "Default")
        self.assertEqual(self.state.seerr_sonarr[0]["activeProfileName"], "Default")
        self.assertEqual(
            set(self.state.radarr_folders),
            {
                "/media/dlna/Movies/By Rating",
                "/media/dlna/Movies/Not Kid Friendly",
                "/media/dlna/Movies/Kid Friendly",
            },
        )
        self.assertEqual(
            set(self.state.sonarr_folders),
            {
                "/media/dlna/TV/By Rating",
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

    def test_plan_library_retarget_sibling_not_kid_folder(self):
        root = self.tmp / "retarget-plan"
        kid = root / "TV" / "Kid Friendly"
        gen = root / "TV" / "Not Kid Friendly"
        auto = root / "TV" / "By Rating"
        dest = gen / "World Trigger" / "Season 02"
        dest.mkdir(parents=True)
        (dest / "World Trigger - S02E01.mkv").write_bytes(b"x" * 80)
        stored = str(kid / "World Trigger")
        action, dest_root = ws.plan_library_retarget(
            stored, [str(auto), str(gen), str(kid)]
        )
        self.assertEqual(action, "retarget")
        self.assertEqual(dest_root, str(gen))
        self.assertTrue(ws.season_videos_on_disk(str(gen / "World Trigger"), 2))
        self.assertFalse(ws.season_videos_on_disk(str(gen / "World Trigger"), 1))

    def test_plan_library_retarget_ambiguous_when_both_libraries_have_files(self):
        root = self.tmp / "retarget-both"
        kid = root / "TV" / "Kid Friendly" / "Show"
        gen = root / "TV" / "Not Kid Friendly" / "Show"
        auto = root / "TV" / "By Rating"
        kid.mkdir(parents=True)
        gen.mkdir(parents=True)
        (kid / "Show.S02E01.mkv").write_bytes(b"x" * 80)
        (gen / "Show.S02E01.mkv").write_bytes(b"y" * 80)
        action, dest = ws.plan_library_retarget(
            str(auto / "Show"), [str(auto), str(gen.parent), str(kid.parent)]
        )
        self.assertEqual(action, "ambiguous")
        self.assertEqual(dest, "")

    def test_housekeep_retargets_hand_moved_season_instead_of_search(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "hand-move"
        kid = root / "TV" / "Kid Friendly" / "World Trigger"
        dest = root / "TV" / "Not Kid Friendly" / "World Trigger" / "Season 02"
        dest.mkdir(parents=True)
        (dest / "World Trigger - S02E01.mkv").write_bytes(b"x" * 100)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "World Trigger",
                "monitored": True,
                "path": str(kid),
                "statistics": {"episodeCount": 8, "episodeFileCount": 0},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "World Trigger"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "World Trigger"},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        out = buf.getvalue()
        self.assertFalse(
            any(item.get("name") == "SeasonSearch" for item in self.state.arr_commands),
            self.state.arr_commands,
        )
        self.assertFalse(
            any(item.get("name") == "EpisodeSearch" for item in self.state.arr_commands)
        )
        rescans = [
            item for item in self.state.arr_commands if item.get("name") == "RescanSeries"
        ]
        self.assertEqual(len(rescans), 1, self.state.arr_commands)
        self.assertEqual(rescans[0].get("seriesId"), 10)
        path = str(self.state.series[0].get("path") or "")
        self.assertTrue(path.startswith(str(root / "TV" / "Not Kid Friendly")))
        self.assertIn("pointed World Trigger", out)
        self.assertIn("rescanning instead of grabbing again", out)

    def test_housekeep_skips_search_when_both_kid_folders_have_the_show(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "hand-move-both"
        kid = root / "TV" / "Kid Friendly" / "Show"
        gen = root / "TV" / "Not Kid Friendly" / "Show"
        kid.mkdir(parents=True)
        gen.mkdir(parents=True)
        (kid / "Show.S02E01.mkv").write_bytes(b"x" * 80)
        (gen / "Show.S02E01.mkv").write_bytes(b"y" * 80)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "Show",
                "monitored": True,
                "path": str(root / "TV" / "By Rating" / "Show"),
                "statistics": {"episodeCount": 8, "episodeFileCount": 0},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(
            any(
                item.get("name") in {"SeasonSearch", "EpisodeSearch", "RescanSeries"}
                for item in self.state.arr_commands
            ),
            self.state.arr_commands,
        )
        self.assertIn("more than one Kid / Not Kid / By Rating folder", buf.getvalue())
        self.assertTrue(
            str(self.state.series[0].get("path") or "").endswith("By Rating/Show")
        )

    def test_housekeep_still_searches_a_season_that_is_not_on_disk(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "real-missing"
        show = root / "TV" / "Not Kid Friendly" / "Silo"
        season1 = show / "Season 01"
        season1.mkdir(parents=True)
        (season1 / "Silo.S01E01.mkv").write_bytes(b"x" * 80)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "Silo",
                "monitored": True,
                "path": str(show),
                "statistics": {"episodeCount": 20, "episodeFileCount": 10},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10, "title": "Silo"},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10, "title": "Silo"},
            },
        ]
        self.assertEqual(ws.housekeep(), 0)
        seasons = [
            item for item in self.state.arr_commands if item.get("name") == "SeasonSearch"
        ]
        self.assertEqual(len(seasons), 1, self.state.arr_commands)
        self.assertEqual(seasons[0].get("seriesId"), 10)
        self.assertEqual(seasons[0].get("seasonNumber"), 2)

    def test_housekeep_rescans_unimported_season_already_at_arr_path(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        root = self.tmp / "stale-scan"
        show = root / "TV" / "Not Kid Friendly" / "Show"
        season = show / "Season 02"
        season.mkdir(parents=True)
        (season / "Show.S02E01.mkv").write_bytes(b"x" * 80)
        os.environ["MEDIA_ROOT"] = str(root)
        self.state.series = [
            {
                "id": 10,
                "title": "Show",
                "monitored": True,
                "path": str(show),
                "statistics": {"episodeCount": 8, "episodeFileCount": 0},
            }
        ]
        self.state.wanted_missing = [
            {
                "id": 1,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 1,
                "series": {"id": 10},
            },
            {
                "id": 2,
                "seriesId": 10,
                "seasonNumber": 2,
                "episodeNumber": 2,
                "series": {"id": 10},
            },
        ]
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ws.housekeep(), 0)
        self.assertFalse(
            any(item.get("name") == "SeasonSearch" for item in self.state.arr_commands),
            self.state.arr_commands,
        )
        rescans = [
            item for item in self.state.arr_commands if item.get("name") == "RescanSeries"
        ]
        self.assertEqual(len(rescans), 1, self.state.arr_commands)
        self.assertIn("already has library video", buf.getvalue())


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
        self.assertEqual(dests["Unknown"], "/media/Movies/Not Kid Friendly")
        self.assertEqual(dests["Kid Show"], "/media/TV/Kid Friendly")
        self.assertEqual(dests["Kid Pathless"], "/media/TV/Kid Friendly")
        self.assertEqual(dests["Adult Show"], "/media/TV/Not Kid Friendly")
        self.assertNotIn("Already Kid", dests)
        kid = next(m for m in self.state.moved if m.get("title") == "Kid Flick")
        self.assertEqual(kid.get("path"), "/media/Movies/Kid Friendly/Kid Flick")
        self.assertNotEqual(
            kid.get("path"),
            "/media/Movies/Not Kid Friendly/Kid Flick",
        )
        editor = [
            call
            for call in self.state.calls
            if call[1] == "PUT" and str(call[2]).endswith("/movie/editor")
        ]
        self.assertTrue(editor)
        self.assertTrue(editor[0][3].get("moveFiles"))
        self.assertNotIn(
            "path",
            editor[0][3],
        )

    def test_arr_v4_still_routes_kid_titles(self):
        self.state.arr_drop_v3 = True
        rr.route_movies("radarr-key")
        rr.route_series("sonarr-key")
        dests = {m.get("title"): m.get("rootFolderPath") for m in self.state.moved}
        self.assertEqual(dests["Kid Flick"], "/media/Movies/Kid Friendly")
        self.assertEqual(dests["Kid Show"], "/media/TV/Kid Friendly")

    def test_world_trigger_tv14_stays_general_bluey_moves_to_kid(self):
        """By Rating sorts on Arr/TMDB cert. Kid / Not Kid Friendly are forced.

        World Trigger is TV-14 — not in KID_TV — so Auto sends it to
        Not Kid Friendly. A forced pick is left alone. Movies match TV.
        """
        self.state.series = [
            {
                "id": 20,
                "title": "World Trigger",
                "certification": "TV-14",
                "path": "/media/TV/By Rating/World Trigger",
            },
            {
                "id": 21,
                "title": "Bluey",
                "certification": "TV-Y",
                "path": "/media/TV/By Rating/Bluey",
            },
            {
                "id": 22,
                "title": "Forced Kid Trigger",
                "certification": "TV-14",
                "path": "/media/TV/Kid Friendly/Forced Kid Trigger",
            },
            {
                "id": 23,
                "title": "Forced General Bluey",
                "certification": "TV-Y",
                "path": "/media/TV/Not Kid Friendly/Forced General Bluey",
            },
        ]
        self.state.movies = [
            {
                "id": 30,
                "title": "Auto R",
                "certification": "R",
                "path": "/media/Movies/By Rating/Auto R",
            },
            {
                "id": 31,
                "title": "Auto G",
                "certification": "G",
                "path": "/media/Movies/By Rating/Auto G",
            },
            {
                "id": 32,
                "title": "Forced Kid R",
                "certification": "R",
                "path": "/media/Movies/Kid Friendly/Forced Kid R",
            },
            {
                "id": 33,
                "title": "Forced General G",
                "certification": "G",
                "path": "/media/Movies/Not Kid Friendly/Forced General G",
            },
        ]
        self.state.moved = []
        rr.route_series("sonarr-key")
        rr.route_movies("radarr-key")
        dests = {item.get("title"): item.get("rootFolderPath") for item in self.state.moved}
        self.assertEqual(dests["World Trigger"], "/media/TV/Not Kid Friendly")
        self.assertEqual(dests["Bluey"], "/media/TV/Kid Friendly")
        self.assertEqual(dests["Auto R"], "/media/Movies/Not Kid Friendly")
        self.assertEqual(dests["Auto G"], "/media/Movies/Kid Friendly")
        self.assertNotIn("Forced Kid Trigger", dests)
        self.assertNotIn("Forced General Bluey", dests)
        self.assertNotIn("Forced Kid R", dests)
        self.assertNotIn("Forced General G", dests)
        self.assertFalse(rr.kid_cert("TV-14", rr.KID_TV))

    def test_retarget_path_replaces_known_root(self):
        dest = rr.retarget_path(
            "/media/Movies/Not Kid Friendly/Fletch (1985)",
            "/media/Movies/Kid Friendly",
            ["/media/Movies/Kid Friendly", "/media/Movies/Not Kid Friendly"],
        )
        self.assertEqual(dest, "/media/Movies/Kid Friendly/Fletch (1985)")
        same = rr.retarget_path(
            "/media/Movies/Kid Friendly/Already",
            "/media/Movies/Kid Friendly",
            ["/media/Movies/Kid Friendly", "/media/Movies/Not Kid Friendly"],
        )
        self.assertEqual(same, "/media/Movies/Kid Friendly/Already")


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

    def test_status_ready_clears_need_proton_without_wired(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_PROTON"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Paste the Proton WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        env.pop("POMPEY_STATUS_NEED_PROTON", None)
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertFalse(data["need_proton"])
        self.assertTrue(data["search"])
        self.assertEqual(data["step"], "ready")
        self.assertEqual(data["percent"], 100)
        self.assertEqual({item["state"] for item in data["steps"]}, {"done"})

    def test_status_wired_fetch_does_not_rewind(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        (ready / "wired").write_text("")
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "fetch", "Downloading hidden engines", "30"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["search"])
        self.assertFalse(data["need_proton"])
        self.assertEqual(data["step"], "ready")
        self.assertEqual(data["percent"], 100)
        self.assertEqual(data["label"], "Ready")

    def test_status_wired_heals_stale_paste_and_torn_json(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_PROTON"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Paste the Proton WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        env.pop("POMPEY_STATUS_NEED_PROTON", None)
        (ready / "wired").write_text("")
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "start", "Starting hidden engines", "70"],
            check=True,
            env=env,
        )
        healed = json.loads((ready / "status.json").read_text())
        self.assertTrue(healed["search"])
        self.assertFalse(healed["need_proton"])
        self.assertEqual(healed["step"], "ready")

        (ready / "status.json").write_text("{not json")
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Waiting for Proton handshake", "15"],
            check=True,
            env=env,
        )
        repaired = json.loads((ready / "status.json").read_text())
        self.assertTrue(repaired["search"])
        self.assertEqual(repaired["step"], "ready")
        self.assertEqual(repaired["percent"], 100)

    def test_status_need_proton_explicit_still_shows_paste_when_wired(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        (ready / "wired").write_text("")
        env["POMPEY_STATUS_NEED_PROTON"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "vpn", "Paste the Proton WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["need_proton"])
        self.assertEqual(data["step"], "vpn")
        self.assertIn("Paste", data["label"])

    def test_status_debug_flag_follows_env(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env.pop("POMPEY_DEBUG", None)
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertFalse(data["debug"])
        env["POMPEY_DEBUG"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey-status"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["debug"])


class DebugIngress(unittest.TestCase):
    def test_debug_off_is_404_and_on_proxies_localhost_engines(self):
        off = dbginc.render(enabled=False, www="/usr/share/pompey")
        self.assertIn("return 404", off)
        self.assertNotIn("proxy_pass http://127.0.0.1:7878", off)
        self.assertNotIn("proxy_pass http://127.0.0.1:8989", off)
        self.assertNotIn("proxy_pass http://127.0.0.1:8080", off)
        on = dbginc.render(enabled=True, www="/custom/www")
        self.assertIn("alias /custom/www/debug-shim.js", on)
        self.assertIn("proxy_pass http://127.0.0.1:7878/", on)
        self.assertIn("proxy_pass http://127.0.0.1:8989/", on)
        self.assertIn("proxy_pass http://127.0.0.1:8080/", on)
        self.assertIn("/debug/radarr/", on)
        self.assertIn("/debug/sonarr/", on)
        self.assertIn("/debug/qbittorrent/", on)
        self.assertIn("$http_x_ingress_path", on)
        self.assertIn('src="./', on)
        self.assertIn('src="/', on)
        self.assertIn('.p="./', on)
        self.assertNotIn("__pompey_debug__", on)
        self.assertIn("Accept-Encoding", on)
        self.assertNotIn("return 404", on)

    def test_debug_enabled_parses_common_truthy(self):
        self.assertFalse(dbginc.debug_enabled(""))
        self.assertFalse(dbginc.debug_enabled("false"))
        self.assertTrue(dbginc.debug_enabled("1"))
        self.assertTrue(dbginc.debug_enabled("true"))

    def test_wait_page_debug_links_stay_under_ingress(self):
        html = (ROOT / "pompey/rootfs/usr/share/pompey/index.html").read_text()
        self.assertIn("ingressBase", html)
        self.assertIn('target="_blank"', html)
        self.assertNotIn("http://\" + location.hostname + \":7878", html)
        self.assertNotIn(":8080", html)

    def test_preview_debug_pages_are_standins(self):
        preview = load("preview", ROOT / "tests/preview.py")
        html, ctype = preview.preview_debug_page("/debug/radarr/")
        self.assertIn("text/html", ctype)
        self.assertIn("Radarr", html)
        api, api_type = preview.preview_debug_page("/debug/radarr/api/v3/system/status")
        self.assertIn("json", api_type)
        self.assertIn("Radarr", api)
        self.assertIsNone(preview.preview_debug_page("/"))

    def test_nginx_debug_proxy_rewrites_ingress_and_stays_off_when_disabled(self):
        import shutil
        import socket
        import subprocess
        import tempfile
        from urllib.request import Request, urlopen

        nginx = shutil.which("nginx")
        if not nginx:
            self.skipTest("nginx not installed")

        class Fake(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                if self.path in {"/", "/index.html"}:
                    body = (
                        b"<html><head><title>Radarr</title>"
                        b'<script type="module" src="/index-02e24635035ed28fd7d3.js"></script>'
                        b'<script src="/Content/app.js"></script></head>'
                        b"<body></body></html>"
                    )
                    ctype = "text/html"
                elif self.path.startswith("/index-") or self.path.startswith("/Content/"):
                    body = b'o.p="/";import("/api/v3/system/status");'
                    ctype = "application/javascript"
                elif self.path.startswith("/640-"):
                    body = b"window.__pompeyChunk = true;"
                    ctype = "application/javascript"
                elif self.path.startswith("/api/"):
                    body = b'{"instanceName":"Radarr"}'
                    ctype = "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        backend = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        backend_port = backend.server_address[1]
        work = Path(tempfile.mkdtemp())
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        listen = sock.getsockname()[1]
        sock.close()
        inc = work / "debug.inc"
        inc.write_text(
            dbginc.render(enabled=True, www=str(ROOT / "pompey/rootfs/usr/share/pompey"))
            .replace("127.0.0.1:7878", f"127.0.0.1:{backend_port}")
        )
        conf = work / "nginx.conf"
        (work / "tmp").mkdir()
        conf.write_text(
            "\n".join(
                [
                    "worker_processes 1;",
                    f"error_log {work}/error.log info;",
                    f"pid {work}/nginx.pid;",
                    "events { worker_connections 32; }",
                    "http {",
                    f"  client_body_temp_path {work}/tmp;",
                    f"  proxy_temp_path {work}/tmp;",
                    f"  fastcgi_temp_path {work}/tmp;",
                    f"  uwsgi_temp_path {work}/tmp;",
                    f"  scgi_temp_path {work}/tmp;",
                    "  access_log off;",
                    "  map $http_upgrade $connection_upgrade { default upgrade; '' close; }",
                    f"  server {{ listen 127.0.0.1:{listen}; include {inc}; }}",
                    "}\n",
                ]
            )
        )
        proc = subprocess.Popen(
            [nginx, "-p", str(work), "-c", str(conf), "-e", str(work / "error.log"), "-g", "daemon off;"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            html = ""
            for _ in range(40):
                try:
                    html = urlopen(f"http://127.0.0.1:{listen}/debug/radarr/", timeout=5).read().decode()
                    break
                except OSError:
                    if proc.poll() is not None:
                        err = (work / "error.log").read_text() if (work / "error.log").is_file() else ""
                        self.fail(f"nginx exited {proc.returncode}: {err}")
                    time.sleep(0.05)
            else:
                self.fail("nginx did not accept connections")
            self.assertIn('src="./index-02e24635035ed28fd7d3.js"', html)
            self.assertIn('src="./Content/app.js"', html)
            self.assertNotIn('src="/index-', html)
            self.assertNotIn("__pompey_debug__", html)
            self.assertIn('src="/debug/shim.js"', html)
            self.assertIn('href="/debug/radarr/"', html)
            req = Request(
                f"http://127.0.0.1:{listen}/debug/radarr/",
                headers={"X-Ingress-Path": "/api/hassio_ingress/tok"},
            )
            ingress_html = urlopen(req, timeout=5).read().decode()
            self.assertIn('src="./index-02e24635035ed28fd7d3.js"', ingress_html)
            self.assertIn('src="./Content/app.js"', ingress_html)
            self.assertIn(
                'href="/api/hassio_ingress/tok/debug/radarr/"',
                ingress_html,
            )
            self.assertIn('src="/api/hassio_ingress/tok/debug/shim.js"', ingress_html)
            js = urlopen(
                f"http://127.0.0.1:{listen}/debug/radarr/index-02e24635035ed28fd7d3.js",
                timeout=5,
            ).read().decode()
            self.assertIn('import("./api/v3/system/status")', js)
            self.assertIn('o.p="./"', js)
            self.assertNotIn('o.p="/"', js)
            chunk = urlopen(
                f"http://127.0.0.1:{listen}/debug/radarr/640-6947d4b0d5ef9ee0fef3.js",
                timeout=5,
            ).read().decode()
            self.assertIn("__pompeyChunk", chunk)
            api = urlopen(
                f"http://127.0.0.1:{listen}/debug/radarr/api/v3/system/status",
                timeout=5,
            ).read().decode()
            self.assertIn("Radarr", api)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            backend.shutdown()
            backend.server_close()


class TestsNeverUseBitTorrent(unittest.TestCase):
    def test_torznab_fixture_is_gone(self):
        self.assertFalse((ROOT / "tests/dev/torznab.py").exists())
        self.assertTrue((ROOT / "tests/lib/fake_source.py").is_file())


if __name__ == "__main__":
    unittest.main()
