"""On-demand, unredacted evidence for investigating real request workflows."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
import urllib.request

import pompey_common as api
from pompey_diagnostics import request_progress
from request_policy import identity

_collection = threading.Lock()
MAX_ROWS = 10000
MAX_DETAILS = 200
MAX_BYTES = 12 * 1024 * 1024
FILE_BYTES = 256 * 1024


class Collector:
    def __init__(self, seconds=45):
        self.until = time.monotonic() + seconds
        self.remaining = MAX_BYTES
        self.lock = threading.Lock()
        self.notices = []

    def read(self, name, url, key=None):
        remaining = self.until - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Collection deadline reached")
        request = urllib.request.Request(url, headers={"X-Api-Key": key} if key else {})
        body = bytearray()
        with urllib.request.urlopen(request, timeout=min(2, remaining)) as response:
            while True:
                if time.monotonic() >= self.until:
                    raise TimeoutError("Collection deadline reached")
                chunk = response.read1(65536)
                if not chunk:
                    break
                with self.lock:
                    self.remaining -= len(chunk)
                    if self.remaining < 0:
                        raise ValueError("Report response budget reached")
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("Application response exceeds 2 MiB")
        return json.loads(body)

    def section(self, result, name, operation):
        try:
            result[name] = operation()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result[name] = {"collectionError": str(exc)}
            self.notices.append(name + ": " + str(exc))

    def pages(self, url, key=None, seerr=False):
        rows, total, seen = [], None, set()
        while len(rows) < MAX_ROWS:
            query = ({"take": 100, "skip": len(rows), "filter": "all", "sort": "added"}
                     if seerr else {"page": len(rows) // 100 + 1, "pageSize": 100,
                                    "sortKey": "date", "sortDirection": "descending"})
            if url.endswith("/queue"):
                query = {"page": len(rows) // 100 + 1, "pageSize": 100,
                         "includeUnknownSeriesItems": "true", "includeUnknownMovieItems": "true"}
            # Return collected pages even when a later page is unavailable.
            try:
                data = self.read("pages", url + "?" + urlencode(query), key)
                page = data["results" if seerr else "records"]
                total = data["pageInfo"]["results"] if seerr else data["totalRecords"]
                if not isinstance(page, list) or type(total) is not int:
                    raise ValueError("Invalid page response")
                ids = [row["id"] for row in page]
                if any(ident in seen for ident in ids) or len(ids) != len(set(ids)):
                    raise ValueError("History changed during pagination; duplicate page/record")
                if not page and len(rows) < total:
                    raise ValueError("Incomplete application page")
                rows.extend(page)
                seen.update(ids)
                if len(rows) >= total:
                    return {"records": rows, "total": total, "complete": True}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return {"records": rows, "total": total, "complete": False, "collectionError": str(exc)}
        return {"records": rows, "total": total, "complete": False, "collectionError": "10000-record limit reached"}

    def service(self, name, secrets):
        result = {"started": time.time()}
        key = secrets.get(name + "_api_key")
        host = {"radarr": api.radarr_url, "sonarr": api.sonarr_url,
                "prowlarr": api.prowlarr_url, "qbittorrent": api.qbit_url,
                "seerr": api.seerr_url}[name]()
        if name == "qbittorrent":
            base = host + "/api/v2"
            endpoints = {"torrents": "torrents/info", "preferences": "app/preferences",
                         "transfer": "transfer/info", "logs": "log/main?last_known_id=-1"}
        elif name == "seerr":
            base = host + "/api/v1"
            key = api.seerr_api_key_from_disk()
            endpoints = {"public": "settings/public", "radarrConnections": "settings/radarr",
                         "sonarrConnections": "settings/sonarr"}
        else:
            base = host + ("/api/v1" if name == "prowlarr" else "/api/v3")
            endpoints = {"status": "system/status", "health": "health", "hostSettings": "config/host", "indexers": "indexer",
                         "indexerStatus": "indexerstatus", "downloadClients": "downloadclient"}
            if name == "prowlarr":
                endpoints["applications"] = "applications"
            else:
                endpoints.update(titles="movie" if name == "radarr" else "series",
                                 qualityProfiles="qualityprofile", qualityDefinitions="qualitydefinition",
                                 customFormats="customformat", delayProfiles="delayprofile",
                                 commands="command", pending="queue/pending")
        # Requests/queues/history take priority over ancillary settings.
        if name == "seerr":
            self.section(result, "requests", lambda: self.pages(base + "/request", key, seerr=True))
        elif name != "qbittorrent":
            if name != "prowlarr":
                self.section(result, "titles", lambda: self.read("titles", base + "/" + endpoints.pop("titles"), key))
                self.section(result, "queue", lambda: self.pages(base + "/queue", key))
            self.section(result, "history", lambda: self.pages(base + "/history", key))
        for field, path in endpoints.items():
            self.section(result, field, lambda path=path: self.read(field, base + "/" + path, key))
        if name == "qbittorrent":
            torrents = result.get("torrents")
            if isinstance(torrents, list):
                details = result["details"] = {}
                for torrent in torrents[:MAX_DETAILS]:
                    token = torrent["hash"]
                    details[token] = {}
                    for field in ("properties", "trackers", "files"):
                        self.section(details[token], field, lambda field=field: self.read(field, base + "/torrents/" + field + "?" + urlencode({"hash": token})))
                if len(torrents) > MAX_DETAILS:
                    self.notices.append("Only the first 200 torrents have file/tracker details")
        result["finished"] = time.time()
        return result


def file_tail(path, limit=FILE_BYTES):
    with path.open("rb") as source:
        metadata = os.fstat(source.fileno())
        size = source.seek(0, 2)
        source.seek(max(0, size - limit))
        body = source.read(limit)
    return {"path": str(path), "bytesOnDisk": size, "truncated": size > limit,
            "modified": metadata.st_mtime, "text": body.decode(errors="replace")}


def request_evidence(services, searches):
    result = []
    torrents = services.get("qbittorrent", {}).get("torrents", [])
    torrents = {str(row.get("hash", "")).lower(): row for row in torrents} if isinstance(torrents, list) else {}
    titles = {}
    by_title = {}
    for kind in ("radarr", "sonarr"):
        rows = services.get(kind, {}).get("titles", [])
        for row in rows if isinstance(rows, list) else []:
            titles[("movie" if kind == "radarr" else "tv", str(row.get("tmdbId" if kind == "radarr" else "tvdbId")))] = (kind, row)
        for field in ("queue", "history"):
            for row in services.get(kind, {}).get(field, {}).get("records", []):
                ident = row.get("seriesId" if kind == "sonarr" else "movieId")
                by_title.setdefault((kind, ident, field), []).append(row)
    for request in services.get("seerr", {}).get("requests", {}).get("records", []):
        found = titles.get(identity(request))
        kind, title = found if found else ("sonarr" if request.get("type") == "tv" else "radarr", None)
        service = services.get(kind, {})
        ident = (title or {}).get("id")
        queue = by_title.get((kind, ident, "queue"), []) if ident is not None else []
        history = by_title.get((kind, ident, "history"), []) if ident is not None else []
        last_search = searches.get(f"search-{kind}-{ident}")
        explained = {**title, "kind": kind, "lastSearch": last_search,
                     "hasFile": title.get("hasFile") if kind == "radarr" else (title.get("statistics") or {}).get("episodeFileCount", 0)} if title else None
        phase, action, messages = request_progress(request, explained, queue, torrents)
        # A partial collection is not evidence that Arr lost a title/download.
        available = isinstance(service.get("titles"), list) and service.get("queue", {}).get("complete") is True
        result.append({"requestId": request.get("id"), "identity": identity(request),
                       "title": (title or {}).get("title"), "arr": kind, "arrId": ident,
                       "observedPhase": phase if available else "Evidence incomplete; inspect collection errors",
                       "nextAction": action if available else None, "messages": messages,
                       "request": request, "queueIds": [row.get("id") for row in queue],
                       "historyIds": [row.get("id") for row in history], "historyComplete": service.get("history", {}).get("complete", False),
                       "lastExplicitSearch": last_search,
                       "downloadIds": list(dict.fromkeys(str(row.get("downloadId", "")).lower() for row in [*queue, *history] if row.get("downloadId")))})
    return result


def collect_report():
    if not _collection.acquire(blocking=False):
        raise BlockingIOError("Another diagnostic report is being collected. Try again shortly.")
    try:
        return _collect_report()
    finally:
        _collection.release()


def _collect_report():
    from pompey_preferences import current
    from pompey_state import directory

    collector = Collector()
    started = datetime.now(timezone.utc).isoformat()
    services, local, searches = {}, {}, {}
    try:
        secrets = api.load_secrets()
    except (OSError, ValueError) as exc:
        secrets = {}
        collector.notices.append("Application credentials unavailable: " + str(exc))
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {name: pool.submit(collector.service, name, secrets)
                   for name in ("seerr", "radarr", "sonarr", "prowlarr", "qbittorrent")}
        for name, future in futures.items():
            collector.section(services, name, future.result)
    # Retain actual monitored/available episode coordinates and imported media info.
    requests = services.get("seerr", {}).get("requests", {}).get("records", [])
    wanted = {identity(row) for row in requests if row.get("status") in {1, 2, 4}}
    series = services.get("sonarr", {}).get("titles", [])
    episodes = services.setdefault("sonarr", {}).setdefault("requestedSeriesDetails", {})
    if isinstance(series, list):
        selected = [row for row in series if ("tv", str(row.get("tvdbId"))) in wanted]
        for row in selected[:MAX_DETAILS]:
            evidence = episodes[str(row["id"])] = {}
            for endpoint in ("episode", "episodefile"):
                collector.section(evidence, endpoint, lambda endpoint=endpoint, row=row: collector.read(endpoint,
                    api.sonarr_url() + "/api/v3/" + endpoint + "?" + urlencode({"seriesId": row["id"]}), secrets.get("sonarr_api_key")))
        if len(selected) > MAX_DETAILS:
            collector.notices.append("Only the first 200 requested series have episode/file detail")
    paths = [Path(api.ready_dir()) / name for name in ("status.json", "health.json", "configuration.json", "storage.json", "vpn-recovery.json")]
    paths += [Path("/usr/share/pompey/engines.json")]
    state_files = sorted(directory().glob("*.json"))
    if len(state_files) > 300:
        collector.notices.append("Only the first 300 policy/search state files were collected")
    paths += state_files[:300]
    file_budget = 2 * 1024 * 1024
    for path in paths:
        if path.is_symlink():
            continue
        if file_budget <= 0:
            collector.notices.append("Local state-file budget reached; remaining files omitted")
            break
        collector.section(local, str(path), lambda path=path: file_tail(path, 65536))
        file_budget -= min(local[str(path)].get("bytesOnDisk", 0), 65536)
        record = local[str(path)]
        if path.stem.startswith("search-") and "text" in record and not record["truncated"]:
            collector.section(searches, path.stem, lambda record=record: json.loads(record["text"]))
    logs = {}
    config = Path(os.environ.get("POMPEY_CONFIG", "/config"))
    for name in ("radarr", "sonarr", "prowlarr", "seerr", "qBittorrent"):
        directory_path = config / name / "logs"
        files = []
        for path in directory_path.glob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    files.append((path, path.stat().st_mtime))
            except OSError:
                collector.notices.append(name + ": a log rotated during collection")
        files.sort(key=lambda entry: ("debug" in entry[0].name.lower(), entry[1]), reverse=True)
        logs[name] = []
        for path, _ in files[:6]:
            record = {}
            collector.section(record, "file", lambda path=path: file_tail(path))
            logs[name].append(record)
        if not files:
            collector.notices.append(name + ": no native log files available")
        elif len(files) > 6:
            collector.notices.append(name + ": older native log files omitted (six-file limit)")
    captured = Path(api.ready_dir()) / "captured-logs"
    for path in sorted(captured.glob("*.log*")):
        if not path.is_symlink():
            collector.section(logs, "captured-" + path.name, lambda path=path: file_tail(path))
    report = {"schema": "pompey-development-diagnostics-v1", "started": started,
              "finished": datetime.now(timezone.utc).isoformat(), "redacted": False,
              "purpose": "Investigate request decisions and derive integration regressions from observed application evidence.",
              "requestCoverage": {"collected": len(requests), **{key: value for key, value in services.get("seerr", {}).get("requests", {}).items() if key != "records"}},
              "limits": {"seconds": 45, "apiBytes": MAX_BYTES, "recordsPerCollection": MAX_ROWS,
                         "logBytesPerFile": FILE_BYTES, "filesPerApplication": 6},
              "interpretation": ["This is a live, non-transactional collection, not a recording of every past state.",
                  "Current torrent seed counts do not establish seed counts when a release was selected.",
                  "Grab history records the chosen release. Native debug logs can record accepted/rejected alternatives while retained.",
                  "Last explicit searches are dated observations, not necessarily the candidates used for an automatic grab.",
                  "No new release searches, downloads, retries or application changes were performed.",
                  "Missing, rolled-over, unavailable or truncated evidence must not be treated as proof that an event did not happen."],
              "requests": request_evidence(services, searches),
              "applications": services, "policyAndRuntimeFiles": local, "logs": logs,
              "collectionNotices": collector.notices}
    collector.section(report, "preferences", current)
    # Each raw request already lives with its correlated explanation above.
    services.get("seerr", {}).get("requests", {}).pop("records", None)
    return "Pompey development diagnostics\n\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n"
