"""Explicit native release rejection, with durable decision and outcome evidence."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlencode

import pompey_common as api
import pompey_state as state
from pompey_runtime import stack_lock

MAX_SEQUENCES = 50
MAX_EVENTS = 200
REASONS = {"stalled", "wrong_content", "wrong_language", "quality", "files", "other"}


def event_time(row):
    return datetime.fromisoformat(row["date"].replace("Z", "+00:00")).timestamp()


def native_data(row):
    return {str(k).lower(): v for k, v in (row.get("data") or {}).items()}


def api_for(kind, timeout=8):
    if kind not in {"radarr", "sonarr"}:
        raise ValueError("Choose a release managed by Radarr or Sonarr")
    key = api.load_secrets()[kind + "_api_key"]
    base = (api.radarr_url() if kind == "radarr" else api.sonarr_url()) + "/api/v3"
    return lambda method, path, body=None: api.http(method, base + path, body, headers=api.arr_headers(key), timeout=timeout)


def history(call, kind, ident):
    rows = []
    for page in range(1, 5):
        result = call("GET", "/history?" + urlencode({"page": page, "pageSize": 250,
            "sortKey": "date", "sortDirection": "descending", "includeEpisode": kind == "sonarr", "movieIds" if kind == "radarr" else "seriesIds": ident}))
        current = api.object_list(result["records"], "release history", ("id",))
        id_key = "movieId" if kind == "radarr" else "seriesId"
        if any(r.get(id_key) != ident for r in current):
            raise ValueError("Native history does not match the selected title")
        rows.extend(current)
        if len(rows) >= result["totalRecords"]:
            return rows
    raise ValueError("Native history exceeds the safe inspection limit; use the advanced application interface")


def files(call, kind, ident):
    rows = api.object_list(call("GET", "/" + ("moviefile?movieId=" if kind == "radarr" else "episodefile?seriesId=") + str(ident)), "library files", ("id",))
    if any(r.get("movieId" if kind == "radarr" else "seriesId") != ident for r in rows):
        raise ValueError("Native files do not match the selected title")
    return rows


def inside(path, root):
    candidate = Path(path).resolve()
    base = Path(root).resolve()
    if candidate == base or not candidate.is_relative_to(base):
        raise ValueError("The selected file is outside Pompey's managed media paths")
    return str(candidate)


def inspect_target(payload):
    kind, ident = payload.get("kind"), payload.get("id")
    if type(ident) is not int or ident <= 0:
        raise ValueError("Choose an existing movie or series")
    call = api_for(kind)
    title = call("GET", ("/movie/" if kind == "radarr" else "/series/") + str(ident))
    rows = history(call, kind, ident)
    token = payload.get("downloadId", "")
    selected_file = payload.get("fileId")
    if selected_file is not None:
        if type(selected_file) is not int or selected_file <= 0:
            raise ValueError("Invalid library file")
        imported = next((r for r in rows if r.get("eventType") == "downloadFolderImported"
                         and str(native_data(r).get("fileid")) == str(selected_file)), None)
        if not imported:
            raise ValueError("The library file has no identifiable native import history")
        token = imported.get("downloadId", "")
    if not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", token):
        raise ValueError("This release has no identifiable torrent download")
    token = token.lower()
    related = [r for r in rows if str(r.get("downloadId", "")).lower() == token]
    grabs = [r for r in related if r.get("eventType") == "grabbed"]
    if not grabs:
        raise ValueError("Native grab history is missing; no files were changed")
    imported_ids = {str(native_data(r).get("fileid")) for r in related if r.get("eventType") == "downloadFolderImported"}
    library_files = []
    for row in files(call, kind, ident):
        if str(row["id"]) not in imported_ids:
            continue
        path = row.get("path") or str(Path(title["path"]) / row["relativePath"])
        library_files.append({"id": row["id"], "path": inside(path, api.media_root()), "size": row.get("size"), "dateAdded": row.get("dateAdded")})
    if selected_file is not None and selected_file not in {r["id"] for r in library_files}:
        raise ValueError("This library file has changed or is no longer present. Refresh the release list")
    queue = call("GET", "/queue?" + urlencode({"page": 1, "pageSize": 1000,
        "includeUnknownSeriesItems": "true", "includeUnknownMovieItems": "true"}))
    queued = [r for r in api.object_list(queue["records"], "download queue", ("id",))
              if str(r.get("downloadId", "")).lower() == token]
    id_key = "movieId" if kind == "radarr" else "seriesId"
    if any(r.get(id_key) != ident for r in queued):
        raise ValueError("This download is shared by multiple titles; review it in the native application")
    torrents = api.object_list(api.http("GET", api.qbit_url() + "/api/v2/torrents/info?" + urlencode({"hashes": token}), timeout=3), "torrent", ("hash",))
    torrent = next((r for r in torrents if r["hash"].lower() == token), None)
    download_files = []
    if torrent:
        if torrent.get("category") != kind:
            raise ValueError("The torrent is not in this application's managed category")
        if not queued:
            raise ValueError("Arr is synchronizing or cleaning up this torrent. Refresh after native cleanup or seeding finishes")
        for path_key in ("content_path", "save_path"):
            if torrent.get(path_key):
                inside(torrent[path_key], Path(api.media_root()) / "downloads")
        native_files = api.object_list(api.http("GET", api.qbit_url() + "/api/v2/torrents/files?" + urlencode({"hash": token}), timeout=3), "torrent files", ("name",))
        download_files = [{"name": r["name"], "size": r.get("size")} for r in native_files]
    # Exact native identities and paths, not changing progress counters, authorize
    # the confirmation. A new import or replacement invalidates an old preview.
    target = {"kind": kind, "id": ident, "downloadId": token,
              "grabId": grabs[0]["id"], "removeDownload": bool(torrent), "queueIds": sorted(r["id"] for r in queued),
              "libraryFiles": sorted(library_files, key=lambda r: r["id"]),
              "downloadFiles": download_files,
              "episodeIds": sorted({r["episodeId"] for r in grabs if r.get("episodeId")}),
              "title": title["title"], "release": grabs[0].get("sourceTitle"),
              "identity": {key: title.get(key) for key in ("tmdbId", "tvdbId", "imdbId")}}
    target["token"] = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
    return target, call, title, rows, torrent


def releases(payload):
    kind, ident = payload.get("kind"), payload.get("id")
    if type(ident) is not int or ident <= 0:
        raise ValueError("Choose an existing movie or series")
    call = api_for(kind)
    title = call("GET", ("/movie/" if kind == "radarr" else "/series/") + str(ident))
    rows = history(call, kind, ident)
    current = {str(r["id"]): r for r in files(call, kind, ident)}
    groups = {}
    for row in rows:
        if row.get("eventType") != "downloadFolderImported":
            continue
        file_id = str(native_data(row).get("fileid"))
        token = str(row.get("downloadId", "")).lower()
        if file_id not in current or not re.fullmatch(r"[a-f0-9]{40}", token):
            continue
        group = groups.setdefault(token, {"downloadId": token, "release": row.get("sourceTitle"), "files": []})
        file = current.pop(file_id)
        path = file.get("path") or str(Path(title["path"]) / file["relativePath"])
        group["files"].append({"id": file["id"], "path": inside(path, api.media_root())})
    return {"choices": list(groups.values()), "unidentifiedFiles": len(current)}


def preview(payload):
    target, *_ = inspect_target(payload)
    target["confirmationId"] = uuid.uuid4().hex
    return target


def sequence_paths():
    return sorted(state.directory().glob("release-sequence-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def read_sequence(path):
    return json.loads(path.read_text())


def save_sequence(sequence):
    sequence["updated"] = time.time()
    state.save("release-sequence-" + sequence["sequenceId"], sequence)


def summarize(sequence):
    return {key: sequence.get(key) for key in ("sequenceId", "kind", "id", "title", "started", "updated", "status", "collectionError", "episodeIds")} | {
        "attempts": [{key: a.get(key) for key in ("release", "downloadId", "reason", "note", "at", "stage", "error")} for a in sequence["attempts"]],
        "outcomes": [{key: r.get(key) for key in ("id", "date", "eventType", "sourceTitle", "downloadId", "episodeId")} for r in sequence.get("nativeHistory", [])]}


def sequences():
    return [summarize(read_sequence(p)) for p in sequence_paths()]


def choose_sequence(target):
    paths = sequence_paths()
    for path in paths:
        item = read_sequence(path)
        if (item["kind"], item["id"]) == (target["kind"], target["id"]) and (
                target["kind"] == "radarr" or set(item["episodeIds"]) & set(target["episodeIds"])):
            if time.time() - item["updated"] < 30 * 86400:
                return item
    if len(paths) >= MAX_SEQUENCES:
        # Retain unfinished incidents; only age out the oldest imported outcome.
        old = next((p for p in reversed(paths) if read_sequence(p)["status"] == "Imported"), None)
        if old is None:
            raise ValueError("The recovery journal is full. Export diagnostics before starting another sequence")
        retention = state.load("release-retention")
        retention["prunedImportedSequences"] = retention.get("prunedImportedSequences", 0) + 1
        state.save("release-retention", retention)
        old.unlink()
    return {"sequenceId": uuid.uuid4().hex, "kind": target["kind"], "id": target["id"], "title": target["title"],
            "identity": target["identity"], "episodeIds": target["episodeIds"], "started": time.time(),
            "status": "Retrying", "attempts": [], "nativeHistory": []}


def compact_history(row):
    result = {k: row.get(k) for k in ("id", "date", "eventType", "sourceTitle", "downloadId", "quality", "languages", "episodeId")}
    result["data"] = {k: v for k, v in native_data(row).items() if k in {
        "fileid", "droppedpath", "importedpath", "indexer", "indexerid", "releasegroup", "customformatscore", "age", "agehours", "size", "message"}}
    return result


def merge_history(sequence, rows):
    rejected = {a["downloadId"] for a in sequence["attempts"]}
    known = {r["id"] for r in sequence["nativeHistory"]}
    for row in sorted(rows, key=lambda r: r["date"]):
        if row["id"] in known:
            continue
        if sequence["kind"] == "sonarr" and row.get("episodeId") not in sequence["episodeIds"]:
            continue
        if event_time(row) < int(sequence["started"]) and str(row.get("downloadId", "")).lower() not in rejected:
            continue
        if len(sequence["nativeHistory"]) >= MAX_EVENTS:
            sequence["historyTruncated"] = True
            break
        sequence["nativeHistory"].append(compact_history(row))
    sequence["nativeHistory"].sort(key=lambda r: r["date"])
    imported = [r for r in sequence["nativeHistory"] if r["eventType"] == "downloadFolderImported"
                and str(r.get("downloadId", "")).lower() not in rejected and event_time(r) >= int(sequence["attempts"][-1]["at"])]
    complete = bool(imported) if sequence["kind"] == "radarr" else set(sequence["episodeIds"]).issubset({r.get("episodeId") for r in imported})
    if complete:
        sequence["status"] = "Imported"  # Technical success, never inferred human approval.
    sequence.pop("collectionError", None)


def reject(payload):
    if payload.get("confirm") is not True:
        raise ValueError("Confirm the exact release and affected files")
    reason, note = payload.get("reason", "other"), payload.get("note", "")
    if reason not in REASONS or not isinstance(note, str) or len(note) > 500:
        raise ValueError("Choose a rejection reason and keep notes under 500 characters")
    confirmation = payload.get("confirmationId", "")
    if not isinstance(confirmation, str) or not re.fullmatch(r"[a-f0-9]{32}", confirmation):
        raise ValueError("Review the affected files before confirming")
    with stack_lock(blocking=False):
        storage_path = Path(api.ready_dir()) / "storage.json"
        if not storage_path.exists() or json.loads(storage_path.read_text()).get("ok") is not True:
            raise ValueError("Storage must be healthy before removing a release")
        target, call, title, rows, torrent = inspect_target(payload)
        if payload.get("token") != target["token"]:
            raise ValueError("The affected files changed. Review a fresh preview before trying again")
        sequence = choose_sequence(target)
        if any(a["confirmationId"] == confirmation for a in sequence["attempts"]):
            raise ValueError("This operation was already recorded. Refresh to inspect its outcome")
        if len(sequence["attempts"]) >= 20:
            raise ValueError("This release sequence reached its safety limit. Export it for investigation")
        attempt = {"at": time.time(), "confirmationId": confirmation, "token": target["token"], "release": target["release"], "downloadId": target["downloadId"],
                   "reason": reason, "note": note, "target": target, "stage": "prepared", "removedFileIds": [],
                   "downloadObservation": {k: torrent.get(k) for k in ("state", "progress", "num_seeds", "num_leechs", "num_complete", "dlspeed")} if torrent else None,
                   "lastExplicitSearch": state.load("search-" + target["kind"] + "-" + str(target["id"]))}
        sequence["attempts"].append(attempt); sequence["status"] = "Retrying"
        merge_history(sequence, rows)
        save_sequence(sequence)  # Persist user intent before any destructive call.
        try:
            policy = call("GET", "/config/downloadclient")
            if policy.get("autoRedownloadFailed") is not False:
                raise ValueError("The managed download policy is not applied yet. Wait for configuration to finish")
            profile = call("GET", "/qualityprofile/" + str(title["qualityProfileId"]))
            attempt["qualityProfile"] = profile
            try:
                candidates = api.object_list(call("GET", "/release?" + urlencode({"movieId" if target["kind"] == "radarr" else "seriesId": target["id"]})), "release candidates", ("title",))
                attempt["candidateSnapshot"] = {"observedAt": time.time(), "meaning": "Fresh search before rejection; not the original automatic selection list", "total": len(candidates),
                    "releases": [{k: r.get(k) for k in ("title", "guid", "approved", "rejections", "seeders", "leechers", "size", "quality", "customFormatScore", "customFormats", "languages", "indexer")} for r in candidates[:30]]}
            except (RuntimeError, OSError, ValueError) as exc:
                attempt["candidateSnapshot"] = {"collectionError": str(exc)}
            save_sequence(sequence)
            fresh, *_ = inspect_target(payload)
            if fresh["token"] != target["token"]:
                raise ValueError("The affected files changed during inspection; review a fresh preview")
            # Arr owns the actual deletion, including associated media files.
            # There is no guessed filesystem traversal or direct unlink here.
            for file in target["libraryFiles"]:
                current = call("GET", "/" + ("moviefile/" if target["kind"] == "radarr" else "episodefile/") + str(file["id"]))
                path = current.get("path") or str(Path(title["path"]) / current["relativePath"])
                if (current.get("movieId" if target["kind"] == "radarr" else "seriesId") != target["id"]
                        or inside(path, api.media_root()) != file["path"] or current.get("size") != file["size"]):
                    raise ValueError("A library file changed before removal; refresh and review it again")
                call("DELETE", "/" + ("moviefile/" if target["kind"] == "radarr" else "episodefile/") + str(file["id"]))
                attempt["removedFileIds"].append(file["id"]); attempt["stage"] = "removing"
                save_sequence(sequence)
            if target["queueIds"]:
                call("DELETE", "/queue/" + str(target["queueIds"][0]) + "?" + urlencode({"removeFromClient": str(target["removeDownload"]).lower(), "blocklist": "true", "skipRedownload": "false"}))
            else:
                call("POST", "/history/failed/" + str(target["grabId"]), {})
            attempt["stage"] = "blocked"
            save_sequence(sequence)
            # Native failure auto-retry is deliberately disabled in Pompey. This
            # explicit user action submits one native search, with Arr choosing.
            if target["kind"] == "radarr":
                command = {"name": "MoviesSearch", "movieIds": [target["id"]]}
            else:
                if not target["episodeIds"]:
                    raise ValueError("The original grab has no episode identities")
                command = {"name": "EpisodeSearch", "episodeIds": target["episodeIds"]}
                episodes = call("GET", "/episode?seriesId=" + str(target["id"]))
                selected = [e for e in episodes if e["id"] in target["episodeIds"]]
                seasons = {e["seasonNumber"] for e in selected}
                if len(seasons) == 1:
                    season = next(iter(seasons))
                    if {e["id"] for e in episodes if e["seasonNumber"] == season} == set(target["episodeIds"]):
                        command = {"name": "SeasonSearch", "seriesId": target["id"], "seasonNumber": season}
            # Refuse to hide an import that raced with deletion. It needs a new
            # preview; never remove a newly discovered file without consent.
            remaining = {str(native_data(r).get("fileid")) for r in history(call, target["kind"], target["id"])
                         if r.get("eventType") == "downloadFolderImported" and str(r.get("downloadId", "")).lower() == target["downloadId"]}
            if any(str(f["id"]) in remaining for f in files(call, target["kind"], target["id"])):
                raise ValueError("This release imported another file during removal. Review a fresh preview")
            attempt["search"] = {"request": command, "response": call("POST", "/command", command)}
            attempt["stage"] = "retry-requested"
            save_sequence(sequence)
        except (RuntimeError, OSError, ValueError) as exc:
            attempt["stage"] = "needs-review"; attempt["error"] = str(exc)
            sequence["status"] = "Needs attention"; save_sequence(sequence)
            raise ValueError("Recovery was interrupted. Its completed steps are saved; refresh before taking further action. " + str(exc)) from exc
    return {"ok": True, "sequenceId": sequence["sequenceId"], "message": "Release rejected. Arr is looking for a replacement."}


def reconcile(maximum=3):
    """Read native history in the existing locked maintenance pass; never retry a mutation."""
    inspected = 0
    for path in reversed(sequence_paths()):
        sequence = read_sequence(path)
        if sequence["status"] == "Imported" or time.time() - sequence["started"] > 30 * 86400:
            continue
        if inspected >= maximum:
            break
        inspected += 1
        try:
            call = api_for(sequence["kind"], timeout=2)
            merge_history(sequence, history(call, sequence["kind"], sequence["id"]))
            attempt = sequence["attempts"][-1]
            search = attempt.get("search")
            if search and sequence["status"] != "Imported":
                command = call("GET", "/command/" + str(search["response"]["id"]))
                search["lastObservation"] = command
                rejected = {a["downloadId"] for a in sequence["attempts"]}
                grabbed = any(r["eventType"] == "grabbed" and str(r.get("downloadId", "")).lower() not in rejected
                              and event_time(r) >= int(attempt["at"]) for r in sequence["nativeHistory"])
                if command.get("status") == "failed":
                    sequence["status"] = "Needs attention"
                    sequence["collectionError"] = "The native replacement search failed. Review Home Assistant Logs."
                elif command.get("status") == "completed" and not grabbed:
                    sequence["status"] = "Waiting for a release"
        except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            sequence["collectionError"] = str(exc)
        save_sequence(sequence)
