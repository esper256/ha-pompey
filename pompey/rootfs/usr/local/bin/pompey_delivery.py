"""Durable delivery receipts, independent of where users subsequently keep media.

Arr still owns files and imports. Receipts suppress automatic monitoring only;
they never authorize filesystem or torrent deletion.
"""

from pathlib import Path
import json
import time
from urllib.parse import urlencode

import pompey_common as api
import pompey_state as state


def identity(kind, title):
    external = title.get("tmdbId" if kind == "radarr" else "tvdbId")
    return f"{kind}:{external}" if type(external) is int and external > 0 else None


def episode_key(episode):
    season, number = episode.get("seasonNumber"), episode.get("episodeNumber")
    if type(season) is int and type(number) is int and season >= 0 and number > 0:
        return f"{season}:{number}"
    return None


def final_path(path, kind):
    roots = (api.movies_dir(), api.movies_kid_dir()) if kind == "radarr" else (api.tv_dir(), api.tv_kid_dir())
    from media_policy import contained
    return bool(path) and any(contained(path, root) for root in roots)


def record_file(receipts, key, file, path, *, historical=False, download_id=None, excluded=None):
    prior = receipts.get(key, {})
    excluded = excluded or {}
    rejected_files = set(prior.get("rejectedFileIds", [])) | set(excluded.get("rejectedFileIds", []))
    rejected_downloads = set(prior.get("rejectedDownloadIds", [])) | set(excluded.get("rejectedDownloadIds", []))
    if file.get("id") in rejected_files or download_id and download_id in rejected_downloads:
        return False
    if prior.get("status") == "delivered":
        return True
    receipts[key] = {**prior, "status": "delivered", "at": time.time(), "fileId": file.get("id"),
                     "path": str(path), "historical": historical, "downloadId": download_id}
    return True


def import_rejections(data):
    """Honor 0.6.0 recovery journals before backfilling new delivery receipts."""
    if data.get("rejectionsImported"):
        return
    for path in state.directory().glob("release-sequence-*.json"):
        sequence = json.loads(path.read_text())
        token = identity(sequence["kind"], sequence.get("identity", {}))
        if not token:
            continue
        entry = data.setdefault("titles", {}).setdefault(token, {"receipts": {}})
        for attempt in sequence["attempts"]:
            target = attempt.get("target", {})
            entry["rejectedFileIds"] = sorted(set(entry.get("rejectedFileIds", [])) | {f["id"] for f in target.get("libraryFiles", [])})
            entry["rejectedDownloadIds"] = sorted(set(entry.get("rejectedDownloadIds", [])) | {attempt["downloadId"]})
    data["rejectionsImported"] = True


def recent_history(call, kind, ident):
    # Only positive import evidence is used. Truncation means no backfill for
    # older entries, never permission to infer completion or remove anything.
    payload = call("GET", "/history?" + urlencode({"page":1, "pageSize":250,
        "sortKey":"date", "sortDirection":"descending", "includeEpisode":kind == "sonarr",
        "movieIds" if kind == "radarr" else "seriesIds":ident}))
    rows = api.object_list(payload["records"], "delivery history", ("id",))
    field = "movieId" if kind == "radarr" else "seriesId"
    if any(row.get(field) != ident for row in rows):
        raise ValueError("Delivery history does not match the selected title")
    return rows


def reopen(target):
    """Called under the recovery lock after durable intent, before native removal."""
    data = state.load("deliveries")
    import_rejections(data)
    token = identity(target["kind"], target["identity"])
    if not token:
        return
    title = data.setdefault("titles", {}).setdefault(token, {})
    keys = ["movie"] if target["kind"] == "radarr" else [f"{e['season']}:{e['episode']}" for e in target.get("episodes", [])]
    receipts = title.setdefault("receipts", {})
    for key in keys:
        previous = receipts.get(key, {})
        receipts[key] = {**previous, "status": "retrying", "reopened": time.time(),
                         "rejectedFileIds": sorted(set(previous.get("rejectedFileIds", [])) | {f["id"] for f in target["libraryFiles"]} | ({previous["fileId"]} if previous.get("fileId") is not None else set())),
                         "rejectedDownloadIds": sorted(set(previous.get("rejectedDownloadIds", [])) | {target["downloadId"]})}
    title["checked"] = 0
    state.save("deliveries", data)


def reconcile():
    """Run inside the existing download job/lock; bound detailed TV/history reads."""
    from pompey_recovery import api_for, native_data
    data = state.load("deliveries")
    import_rejections(data)
    saved = data.setdefault("titles", {})
    for kind in ("radarr", "sonarr"):
        detail_budget = 1 if kind == "radarr" else 2
        call = api_for(kind, timeout=3)
        rows = api.object_list(call("GET", "/movie" if kind == "radarr" else "/series"), "delivery titles", ("id",))
        rows.sort(key=lambda r: saved.get(identity(kind, r), {}).get("checked", 0))
        for row in rows:
            token = identity(kind, row)
            if not token:
                continue
            entry = saved.setdefault(token, {"receipts": {}})
            receipts = entry["receipts"]
            entry.update(id=row["id"], title=row.get("title"))
            if kind == "radarr":
                file = row.get("movieFile") or {}
                path = file.get("path") or str(Path(row.get("path", "")) / file.get("relativePath", ""))
                if row.get("hasFile") and file.get("id") and final_path(path, kind) and Path(path).is_file():
                    record_file(receipts, "movie", file, path, excluded=entry)
                if not receipts.get("movie") and detail_budget and time.time() - entry.get("checked", 0) > 3600:
                    # Backfill only a native successful import into a final
                    # library, never a title/name match or a staging import.
                    detail_budget -= 1
                    for event in recent_history(call, kind, row["id"]):
                        details = native_data(event)
                        imported = details.get("importedpath")
                        if event.get("eventType") == "downloadFolderImported" and final_path(imported, kind):
                            ident = details.get("fileid")
                            if record_file(receipts, "movie", {"id": int(ident) if str(ident).isdigit() else None}, imported,
                                           historical=True, download_id=event.get("downloadId"), excluded=entry):
                                break
                    entry["checked"] = time.time()
                if receipts.get("movie", {}).get("status") == "delivered" and row.get("monitored"):
                    state.save("deliveries", data)  # Evidence precedes the monitoring change.
                    call("PUT", "/movie/" + str(row["id"]), dict(row, monitored=False))
            else:
                stats = row.get("statistics") or {}
                fingerprint = [row.get("path"), stats.get("episodeFileCount"), stats.get("sizeOnDisk")]
                if not detail_budget or (entry.get("files") == fingerprint and time.time() - entry.get("checked", 0) <= 60):
                    continue
                detail_budget -= 1
                episodes = api.object_list(call("GET", "/episode?seriesId=" + str(row["id"])), "delivery episodes", ("id",))
                files = {f["id"]: f for f in api.object_list(call("GET", "/episodefile?seriesId=" + str(row["id"])), "delivery files", ("id",))}
                if any(not e.get("hasFile") and episode_key(e) not in receipts for e in episodes) and time.time() - entry.get("historyChecked", 0) > 3600:
                    by_id = {e["id"]: e for e in episodes}
                    for event in recent_history(call, kind, row["id"]):
                        details = native_data(event)
                        key = episode_key(by_id.get(event.get("episodeId"), {}))
                        imported = details.get("importedpath")
                        if key and key not in receipts and event.get("eventType") == "downloadFolderImported" and final_path(imported, kind):
                            ident = details.get("fileid")
                            record_file(receipts, key, {"id": int(ident) if str(ident).isdigit() else None}, imported,
                                        historical=True, download_id=event.get("downloadId"), excluded=entry)
                    entry["historyChecked"] = time.time()
                unmonitor = []
                for episode in episodes:
                    key = episode_key(episode)
                    if not key:
                        continue
                    file = files.get(episode.get("episodeFileId"), {})
                    path = file.get("path") or str(Path(row.get("path", "")) / file.get("relativePath", ""))
                    if file and final_path(path, kind) and Path(path).is_file():
                        record_file(receipts, key, file, path, excluded=entry)
                    if receipts.get(key, {}).get("status") == "delivered" and episode.get("monitored"):
                        unmonitor.append(episode["id"])
                if unmonitor:
                    state.save("deliveries", data)
                    call("PUT", "/episode/monitor", {"episodeIds": unmonitor, "monitored": False})
                entry["checked"] = time.time()
                if all(not e.get("hasFile") or final_path((files.get(e.get("episodeFileId")) or {}).get("path") or str(Path(row.get("path", "")) / (files.get(e.get("episodeFileId")) or {}).get("relativePath", "")), kind) for e in episodes):
                    entry["files"] = fingerprint
    data["updated"] = time.time()
    state.save("deliveries", data)


def for_title(kind, title):
    return state.load("deliveries").get("titles", {}).get(identity(kind, title), {}).get("receipts", {})
