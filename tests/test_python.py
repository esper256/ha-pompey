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
import tempfile


import unittest
from unittest.mock import patch


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


from pathlib import Path


from urllib.parse import parse_qs


os.environ.setdefault("POMPEY_WAIT_TRIES", "8")


os.environ.setdefault("POMPEY_WAIT_SLEEP", "0.01")


ROOT = Path(__file__).resolve().parents[1]


BIN = ROOT / "pompey/rootfs/usr/local/bin"


sys.path.insert(0, str(BIN))


OPTIONS = json.loads((ROOT / "tests/options.json").read_text())


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ws = load("wire_stack", BIN / "wire_stack.py")


recyclarr = load("recyclarr_sync", BIN / "recyclarr_sync.py")


rr = load("route_rating", BIN / "route_rating.py")


wqc = load("wg_quick_contract", ROOT / "tests/lib/wg_quick_contract.py")


emitmod = load("pompey_log_emit", BIN / "pompey_log_emit.py")


vpnstats = load("pompey_vpn_stats", BIN / "pompey_vpn_stats.py")


dbginc = load("write_debug_ingress", BIN / "write_debug_ingress.py")


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
        self.radarr_profiles = [any_quality_bundle(1,"Default")["profile"], any_quality_bundle(2,"Max")["profile"]]
        self.sonarr_profiles = [any_quality_bundle(1,"Default")["profile"], any_quality_bundle(2,"Max")["profile"]]
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
        self.seerr_fail_create_request = False
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
                    new = str((body or {}).get("path") or "").rstrip("/")
                    for folder_path in folders:
                        old = str(folder_path or "").rstrip("/")
                        if old and new and (
                            new == old
                            or new.startswith(old + "/")
                            or old.startswith(new + "/")
                        ):
                            return self._send(
                                400,
                                {"message": f"Folder {new} overlaps {old}"},
                            )
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

                    def uses_root(item) -> bool:
                        assigned = str(item.get("rootFolderPath") or "").rstrip("/")
                        if assigned:
                            return assigned == leftover
                        path = str(item.get("path") or "").rstrip("/")
                        if not ws.in_root(path, leftover):
                            return False
                        return not any(
                            ws.in_root(path, wanted)
                            for wanted in folders
                            if str(wanted).rstrip("/") not in {"", leftover}
                        )

                    still = [item for item in titles if uses_root(item)]
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
                if path == "/api/v1/request" and method == "POST":
                    if state.seerr_fail_create_request:
                        return self._send(500, {"message": "create failed"})
                    payload = body if isinstance(body, dict) else {}
                    existing = []
                    for item in state.seerr_requests:
                        try:
                            existing.append(int(item.get("id")))
                        except (TypeError, ValueError):
                            continue
                    new_id = (max(existing) + 1) if existing else 1
                    media_type = str(payload.get("mediaType") or "movie")
                    row = {
                        "id": new_id,
                        "status": 2,
                        "type": media_type,
                        "is4k": bool(payload.get("is4k")),
                        "media": {
                            "mediaType": media_type,
                            "tmdbId": payload.get("mediaId"),
                        },
                    }
                    for key in ("serverId", "profileId", "rootFolder", "languageProfileId"):
                        if payload.get(key) not in (None, ""):
                            row[key] = payload[key]
                    if payload.get("seasons") is not None:
                        raw = payload.get("seasons")
                        if raw == "all":
                            row["seasons"] = [{"seasonNumber": 1}]
                        elif isinstance(raw, list):
                            row["seasons"] = [
                                {"seasonNumber": n} for n in raw if n is not None
                            ]
                    state.seerr_requests.append(row)
                    return self._send(201, row)
                if path.startswith("/api/v1/request/") and method == "DELETE":
                    tail = path.rsplit("/", 1)[-1]
                    try:
                        rid = int(tail)
                    except ValueError:
                        return self._send(404, {"error": path})
                    state.seerr_requests = [
                        item
                        for item in state.seerr_requests
                        if item.get("id") != rid
                    ]
                    return self._send(204)
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


class LogEmit(unittest.TestCase):
    def setUp(self):
        emitmod._last_emitted.clear()
        emitmod._html_dump.clear()

    def test_suppresses_entire_html_page_and_preserves_following_warning(self):
        lines = ['<!DOCTYPE html>', '<html lang="en">', '<head>', '<style>']
        lines += ['body { color: red; }', 'plain page text', 'window.example = true;'] * 1000
        lines += ['</style>', '</head>', '<body>Unavailable</body>', '</html>', '[Warn] DiskScanService: Disk full']
        out, err = self.captured('Prowlarr', lines)
        self.assertEqual(out, '')
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn('Disk full', err)

    def test_truncated_html_resumes_at_next_log_record(self):
        out, err = self.captured('Prowlarr', ['[Error] Fetch failed: <html>', 'page text',
            '[Info] Application started', '[Error] Database unavailable'])
        self.assertNotIn('page text', out+err)
        self.assertIn('Fetch failed:', err)
        self.assertIn('Application started', out)
        self.assertIn('Database unavailable', err)
        _, err = self.captured('Prowlarr', ['<!DOCTYPE html>', 'page text',
            '2026-09-14 07:13:53.1|Warn| Disk full'])
        self.assertIn('Disk full', err)

    def test_single_line_html_does_not_hide_other_services(self):
        self.captured('Prowlarr',['<html><body>Page</body></html>'])
        out, _ = self.captured('Prowlarr',['Startup complete'])
        self.assertIn('Startup complete', out)
        self.captured('Prowlarr',['<!DOCTYPE html>'])
        _, err = self.captured('Sonarr',['[Error] Disk full'])
        self.assertIn('Disk full', err)

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
                    "need_vpn": False,
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
        self.original_environ = os.environ.copy()
        sync = patch.object(ws, "run_recyclarr", return_value=True)
        sync.start()
        self.addCleanup(sync.stop)
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
                "MEDIA_ROOT": str(self.tmp / "media"),
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
        os.environ.pop("POMPEY_DOTNET_ROOT", None)
        os.environ.pop("POMPEY_FETCH_ENGINES", None)
        os.environ.pop("POMPEY_ENGINE_REFRESH_AGE", None)
        os.environ.pop("POMPEY_HOLD_QBIT", None)
        os.environ.pop("POMPEY_WIRE_TIMEOUT", None)
        os.environ.pop("SIMULTANEOUS_DOWNLOADS", None)
        self.nginx = nginx
        self.ready = ready
        self._old_path = os.environ.get("PATH", "")


    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_environ)
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()


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


    def test_failed_recyclarr_does_not_advertise_legacy_profile_names(self):
        with patch.object(ws, "run_recyclarr", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Recyclarr"):
                ws.main()
        self.assertFalse((self.ready / "wired").exists())

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


    def test_leftover_parent_root_is_pruned_so_household_folders_can_wire(self):
        media = os.environ["MEDIA_ROOT"]
        leftover = f"{media}/Movies"
        auto = f"{media}/Movies/By Rating"
        self.state.radarr_folders = [leftover]
        self.state.folder_ids = {leftover: 9}
        self.state.next_folder_id = 10
        self.state.movies = [
            {"id": 99, "title": "Old Title", "path": f"{leftover}/Old Title"},
        ]
        self.state.import_lists = [
            {"id": 3, "rootFolderPath": leftover},
        ]
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertNotIn(leftover, [str(p).rstrip("/") for p in self.state.radarr_folders])
        self.assertIn(auto, [str(p).rstrip("/") for p in self.state.radarr_folders])
        self.assertEqual(self.state.movies[0]["rootFolderPath"], auto)
        self.assertEqual(self.state.import_lists[0]["rootFolderPath"], auto)
        self.assertTrue((self.ready / "wired").exists())


    def test_wires_without_source_url(self):
        os.environ["INDEXER_URL"] = ""
        os.environ["INDEXER_API_KEY"] = ""
        rc = ws.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.ready / "wired").exists())
        self.assertEqual(self.state.indexers, [])
        self.assertEqual(
            [c.get("name") for c in self.state.commands],
            [],
        )


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


    def test_recyclarr_sets_dotnet_root_when_runtime_is_on_disk(self):
        fake = self.tmp / "recyclarr"
        fake.write_text("#!/bin/sh\necho DOTNET_ROOT=$DOTNET_ROOT\nexit 0\n")
        fake.chmod(0o755)
        runtime = self.tmp / "dotnet-runtime"
        runtime.mkdir()
        (runtime / "dotnet").write_text("")
        os.environ["POMPEY_RECYCLARR"] = str(fake)
        os.environ["POMPEY_DOTNET_ROOT"] = str(runtime)
        os.environ["POMPEY_RECYCLARR_DATA"] = str(self.tmp / "recyclarr-data")
        secrets = {
            "radarr_api_key": "radarr-secret-key",
            "sonarr_api_key": "sonarr-secret-key",
        }
        (self.tmp / "secrets.json").write_text(json.dumps(secrets))
        os.environ["POMPEY_SECRETS"] = str(self.tmp / "secrets.json")
        from io import StringIO
        from contextlib import redirect_stdout

        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(recyclarr.main(), 0)
        self.assertIn(f"DOTNET_ROOT={runtime}", buf.getvalue())


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


    def _stub_nginx(self, script: str) -> None:
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "nginx"
        stub.write_text("#!/bin/sh\n" + script)
        stub.chmod(0o755)
        os.environ["PATH"] = f"{bindir}:{self._old_path}"


class RouteRating(unittest.TestCase):
    def setUp(self):
        self.old_environ = os.environ.copy()
        self.temp = tempfile.TemporaryDirectory(prefix="pompey-rating-")
        os.environ["POMPEY_DATA"] = self.temp.name
        self.state = FakeState()
        self.servers = []
        for role, key in (("radarr", "RADARR_URL"), ("sonarr", "SONARR_URL")):
            httpd, url = start_role(role, self.state)
            self.servers.append(httpd)
            os.environ[key] = url
        os.environ["MEDIA_ROOT"] = "/media"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temp.cleanup()
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


class VPNSetup(unittest.TestCase):
    def setUp(self):
        self.setup = load("pompey_setup", BIN / "pompey_setup.py")
        self.sample = (ROOT / "tests/fixtures/wg0.conf").read_text()

    def test_valid_vpn_file(self):
        self.assertEqual(self.setup.validate_wg(self.sample), "")

    def test_empty_paste(self):
        self.assertTrue(self.setup.validate_wg(""))

    def test_missing_endpoint(self):
        text = "\n".join(
            line for line in self.sample.splitlines() if not line.lower().startswith("endpoint")
        )
        err = self.setup.validate_wg(text)
        self.assertIn("Endpoint", err)

    def test_does_not_echo_private_key(self):
        err = self.setup.validate_wg("[Interface]\nPrivateKey = SUPERSECRET\n")
        self.assertNotIn("SUPERSECRET", err)

    def test_status_need_vpn_flag(self):
        import tempfile

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_VPN"] = "1"
        import subprocess

        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Paste the VPN WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["need_vpn"])

        env.pop("POMPEY_STATUS_NEED_VPN", None)
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "start", "Waiting for hidden engines", "65"],
            check=True,
            env=env,
        )
        stuck = json.loads((ready / "status.json").read_text())
        self.assertTrue(stuck["need_vpn"])
        self.assertEqual(stuck["step"], "vpn")
        self.assertIn("Paste", stuck["label"])
        self.assertEqual(
            [item["state"] for item in stuck["steps"] if item["id"] in ("vpn", "fetch", "start")],
            ["active", "pending", "pending"],
        )

        env["POMPEY_STATUS_NEED_VPN"] = "0"
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Bringing up the VPN tunnel", "10"],
            check=True,
            env=env,
        )
        cleared = json.loads((ready / "status.json").read_text())
        self.assertFalse(cleared["need_vpn"])
        self.assertEqual(cleared["label"], "Bringing up the VPN tunnel")

    def test_status_ready_clears_need_vpn_without_wired(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_VPN"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Paste the VPN WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        env.pop("POMPEY_STATUS_NEED_VPN", None)
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertFalse(data["need_vpn"])
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
            [sys.executable, str(BIN / "pompey_status.py"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        (ready / "wired").write_text("")
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "fetch", "Downloading hidden engines", "30"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["search"])
        self.assertFalse(data["need_vpn"])
        self.assertEqual(data["step"], "ready")
        self.assertEqual(data["percent"], 100)
        self.assertEqual(data["label"], "Ready")

    def test_status_wired_heals_stale_paste_and_torn_json(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        env["POMPEY_STATUS_NEED_VPN"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Paste the VPN WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        env.pop("POMPEY_STATUS_NEED_VPN", None)
        (ready / "wired").write_text("")
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "start", "Starting hidden engines", "70"],
            check=True,
            env=env,
        )
        healed = json.loads((ready / "status.json").read_text())
        self.assertTrue(healed["search"])
        self.assertFalse(healed["need_vpn"])
        self.assertEqual(healed["step"], "ready")

        (ready / "status.json").write_text("{not json")
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Waiting for VPN handshake", "15"],
            check=True,
            env=env,
        )
        repaired = json.loads((ready / "status.json").read_text())
        self.assertTrue(repaired["search"])
        self.assertEqual(repaired["step"], "ready")
        self.assertEqual(repaired["percent"], 100)

    def test_status_need_vpn_explicit_still_shows_paste_when_wired(self):
        import tempfile
        import subprocess

        ready = Path(tempfile.mkdtemp())
        env = os.environ.copy()
        env["POMPEY_READY"] = str(ready)
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        (ready / "wired").write_text("")
        env["POMPEY_STATUS_NEED_VPN"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "vpn", "Paste the VPN WireGuard file you downloaded", "8"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertTrue(data["need_vpn"])
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
            [sys.executable, str(BIN / "pompey_status.py"), "ready", "Ready", "100"],
            check=True,
            env=env,
        )
        data = json.loads((ready / "status.json").read_text())
        self.assertFalse(data["debug"])
        env["POMPEY_DEBUG"] = "1"
        subprocess.run(
            [sys.executable, str(BIN / "pompey_status.py"), "ready", "Ready", "100"],
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
            self.assertNotIn('duplicate MIME type', (work / 'error.log').read_text())
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
