"""Bounded, shared, read-only observations for Ingress; no search or mutations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error

import pompey_common as api

TTL = 15
_lock = threading.Lock()
_sources = {}
_attempted = 0


def get(url, key=None):
    headers = {"X-Api-Key": key} if key else {}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=2) as response:
        body = response.read(16 * 1024 * 1024 + 1)
    if len(body) > 16 * 1024 * 1024:
        raise ValueError("Response exceeds observation limit")
    return json.loads(body)


def rows(value):
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Invalid application response")
    return value


def media_server_settings(base, key, kind):
    """Seerr returns saved library selections, not a live media-server probe."""
    result = {"type": kind, "configured": None, "state": "unknown", "observed": time.time()}
    path = "plex" if kind == 1 else "jellyfin" if kind in {2, 3} else None
    if not path:
        return {**result, "configured": False}
    try:
        settings = get(base + "/settings/" + path, key)
        result["configured"] = bool(settings.get("ip") or settings.get("hostname"))
        if result["configured"]:
            libraries = rows(get(base + "/settings/" + path + "/library", key))
            result["selectedLibraries"] = sum(bool(lib.get("enabled")) for lib in libraries)
            result["state"] = "saved-selections" if result["selectedLibraries"] else "no-libraries"
    except urllib.error.HTTPError as exc:
        result["state"] = "seerr-auth" if exc.code in {401, 403} else "unknown"
        exc.close()
    except (TimeoutError, urllib.error.URLError, ConnectionError):
        result["state"] = "seerr-connection"
    except (OSError, ValueError, KeyError, TypeError):
        result["state"] = "unknown"
    return result


def read_source(name, secrets):
    if name in {"radarr", "sonarr"}:
        host = api.radarr_url() if name == "radarr" else api.sonarr_url()
        base = host.rstrip("/") + "/api/v3"
        key = secrets[name + "_api_key"]
        titles = rows(get(base + ("/movie" if name == "radarr" else "/series"), key))
        queue = get(base + "/queue?page=1&pageSize=100&includeUnknownSeriesItems=true&includeUnknownMovieItems=true"
                    + ("&includeEpisode=true" if name == "sonarr" else ""), key)
        rows(queue["records"])
        return {"titles": titles, "queue": queue, "indexers": rows(get(base + "/indexer", key))}
    if name == "prowlarr":
        base = api.prowlarr_url().rstrip("/") + "/api/v1"
        key = secrets["prowlarr_api_key"]
        result = {"indexers": rows(get(base + "/indexer", key)), "status": rows(get(base + "/indexerstatus", key))}
        indexer_availability(result)
        return result
    if name == "qbittorrent":
        return {"torrents": rows(get(api.qbit_url() + "/api/v2/torrents/info"))}
    base = api.seerr_url().rstrip("/") + "/api/v1"
    public = get(base + "/settings/public")
    result = {"initialized": public.get("initialized") is True}
    if not result["initialized"]:
        return result
    key = api.seerr_api_key_from_disk()
    count = get(base + "/request/count", key)
    if any(type(count.get(k)) is not int or count[k] < 0 for k in ["total", "pending", "approved", "declined", "processing", "available", "completed"]):
        raise ValueError("Invalid request counts")
    recent = get(base + "/request?take=100&skip=0&filter=all&sort=added", key)
    result.update(counts=count, requests=rows(recent["results"]), total=recent["pageInfo"]["results"])
    result["connections"] = {name: bool(rows(get(base + "/settings/" + name, key))) for name in ["radarr", "sonarr"]}
    # Return only optional media-server status, never settings credentials.
    result["mediaServer"] = media_server_settings(base, key, public.get("mediaServerType"))
    return result


def torrent_counts(torrents):
    counts = dict(downloading=0, seeding=0, stalled=0, paused=0, checking=0, other=0)
    for row in torrents:
        state = row.get("state", "")
        if state in {"downloading", "forcedDL", "metaDL", "forcedMetaDL"}:
            bucket = "downloading"
        elif state in {"uploading", "forcedUP", "stalledUP"}:
            bucket = "seeding"
        elif state == "stalledDL":
            bucket = "stalled"
        elif state in {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}:
            bucket = "paused"
        elif state in {"checkingDL", "checkingUP", "checkingResumeData", "moving"}:
            bucket = "checking"
        else:
            bucket = "other"
        counts[bucket] += 1
    return counts


def indexer_availability(data):
    """Native Prowlarr backoff state, not a fresh live probe of every source."""
    enabled = {row["id"]: row for row in data["indexers"] if row.get("enable")}
    failures = {}
    for row in data["status"]:
        until = row.get("disabledTill")
        if until and datetime.fromisoformat(until.replace("Z", "+00:00")).timestamp() > time.time():
            if row["indexerId"] in enabled:
                failures[row["indexerId"]] = {"name": enabled[row["indexerId"]]["name"], "retryAfter": until}
    return {"healthy": len(set(enabled) - failures.keys()), "enabled": len(enabled)}, list(failures.values())


def summarize(sources):
    result = {}
    for name, field in [("radarr", "movies"), ("sonarr", "shows")]:
        source = sources.get(name, {})
        data = source.get("data")
        result[field] = {"value": sum(row.get("monitored") is True for row in data["titles"]) if data else None,
                         "updated": source.get("updated"), "available": source.get("available", False)}
    for name, field in [("qbittorrent", "torrents"), ("prowlarr", "indexers"), ("seerr", "requests")]:
        source = sources.get(name, {})
        data = source.get("data")
        value = None
        if data and name == "qbittorrent":
            value = torrent_counts(data["torrents"])
        if data and name == "prowlarr":
            value, _ = indexer_availability(data)
        if data and name == "seerr" and data.get("counts"):
            c = data["counts"]
            failed = max(0, c["total"] - c["pending"] - c["approved"] - c["declined"] - c["completed"])
            value = {"open": c["pending"] + c["processing"] + failed,
                     "approval": c["pending"], "processing": c["processing"], "failed": failed,
                     "completed": c["completed"] + c["available"], "total": c["total"]}
        result[field] = {"value": value, "updated": source.get("updated"), "available": source.get("available", False)}
    return result


def snapshot(max_age=TTL):
    global _attempted
    with _lock:
        if time.monotonic() - _attempted >= max_age:
            _attempted = time.monotonic()
            secrets = api.load_secrets()
            names = ["radarr", "sonarr", "prowlarr", "qbittorrent", "seerr"]
            with ThreadPoolExecutor(max_workers=5) as pool:
                pending = {name: pool.submit(read_source, name, secrets) for name in names}
                for name, future in pending.items():
                    try:
                        _sources[name] = {"available": True, "updated": time.time(), "data": future.result()}
                    except (OSError, ValueError, KeyError, TypeError):
                        previous = _sources.get(name, {})
                        _sources[name] = {**previous, "available": False}
        # Raw native settings and API responses stay in-process. Only explicitly
        # selected fields are serialized by consumers.
        return dict(_sources)


def overview():
    sources = snapshot()
    manifest = json.loads(Path("/usr/share/pompey/engines.json").read_text())
    seerr = sources.get("seerr", {})
    data = seerr.get("data", {})
    connections = {}
    for name in ["radarr", "sonarr"]:
        source = sources.get(name, {})
        connections[name] = bool(source.get("data", {}).get("indexers")) if source.get("available") else None
    prowlarr = sources.get("prowlarr", {})
    indexer_issues = indexer_availability(prowlarr["data"])[1] if prowlarr.get("available") else []
    storage_path = Path(os.environ.get("POMPEY_READY", "/tmp/pompey")) / "storage.json"
    storage = json.loads(storage_path.read_text()) if storage_path.exists() else {"ok": None, "phase": "unknown"}
    return {"updated": time.time(), "summary": summarize(sources), "storage": storage,
            "setup": {"initialized": data.get("initialized"), "mediaServer": data.get("mediaServer"),
                      "sources": connections, "requestConnections": data.get("connections"), "indexerIssues": indexer_issues, "seerrAvailable": seerr.get("available", False)},
            "versions": {"pompey": os.environ.get("BUILD_VERSION", "unknown"),
                         "build": os.environ.get("BUILD_REF") or None,
                         "applications": {name: row["version"] for name, row in manifest["engines"].items()},
                         "resources": manifest["resources"]}}
