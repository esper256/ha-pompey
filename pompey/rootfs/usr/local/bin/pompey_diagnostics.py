"""Diagnostic information from real service APIs; explicit searches never run on page load."""

import time
import json
from pathlib import Path
from urllib.parse import urlencode
import pompey_common as api
import pompey_state as state


def request_progress(request, title, queue, torrents):
    """Explain only observed native state; series files do not prove fulfilment."""
    status = request.get("status")
    messages = [str(text) for row in queue for group in row.get("statusMessages", [])
                for text in group.get("messages", [])]
    if status not in {1, 2, 3, 4, 5}:
        return "Request state unavailable", "Review this request in Seerr.", messages
    if status == 1:
        return "Awaiting approval", "Approve or decline this request in Seerr.", messages
    if status == 3:
        return "Declined", "Review the request in Seerr.", messages
    if status == 4:
        return "Request failed", "Review the request and its application connection in Seerr.", messages
    if status == 5:
        return "Completed", "", messages
    if not title:
        return "Waiting for Arr", "Check the Seerr application connections.", messages
    if messages and any(row.get("trackedDownloadStatus") in {"warning", "error"} for row in queue):
        return "Import needs attention", "Review the reported problem and file matches in the native importer.", messages
    for row in queue:
        torrent = torrents.get(str(row.get("downloadId", "")).lower(), {})
        native = torrent.get("state", "")
        if native in {"stalledDL", "metaDL", "forcedMetaDL"}:
            return "Waiting for peers" if native == "stalledDL" else "Retrieving torrent metadata", "Wait for a source to connect, or inspect releases to check alternatives. Inspection does not start a download.", messages
        if native in {"error", "missingFiles"}:
            return "Download error", "Check storage access and the downloader error before retrying.", messages
        if native in {"checkingDL", "checkingResumeData"}:
            return "Checking downloaded data", "qBittorrent is verifying the files before continuing.", messages
        if native == "queuedDL":
            return "Waiting for a download slot", "Other downloads have priority under the configured queue limit.", messages
        if native in {"stoppedDL", "pausedDL"}:
            return "Download stopped", "Review the downloader and any skipped files.", messages
        if row.get("sizeleft", 0) > 0:
            return "Downloading", "", messages
        return "Waiting for import", "Review the native queue if this persists.", messages
    media = request.get("media") or {}
    available = media.get("status4k" if request.get("is4k") else "status") == 5
    if available:
        return "Available according to Seerr", "", messages
    if title["kind"] == "radarr" and title.get("hasFile"):
        return "Imported to library", "Check your media player if the title is not visible yet.", messages
    if title["kind"] == "radarr" and title.get("delivered"):
        return "Delivered", "Previously delivered to the library. Later file moves do not reopen this request.", messages
    if title["kind"] == "sonarr" and title.get("hasFile"):
        if media.get("status4k" if request.get("is4k") else "status") == 4:
            return "Partially available", "Review the requested seasons for episodes that are still missing.", messages
        return "Waiting for requested episodes", "The series has library files; these do not establish that the requested seasons are available.", messages
    if title.get("monitored") is False:
        return "Monitoring disabled", "Review monitoring in the native application.", messages
    search = title.get("lastSearch") or {}
    if search.get("status") == "Search complete":
        releases = search.get("releases", [])
        if not releases:
            return "No releases found at last search", "Search again later or review indexer health.", messages
        if all(row.get("rejections") for row in releases):
            return "All releases rejected at last search", "Review the release rejection reasons.", messages
    return "Waiting for a suitable release", "Search releases to inspect availability and rejection reasons.", messages


def download_evidence(torrent):
    """Native counts are observations, not proof that a peer can supply the file."""
    return {key: torrent.get(native) for key, native in {
        "state": "state", "progress": "progress", "speed": "dlspeed",
        "connectedSeeds": "num_seeds", "connectedPeers": "num_leechs",
        "reportedSeeds": "num_complete", "availability": "availability",
        "remainingBytes": "amount_left", "eta": "eta", "added": "added_on",
        "category": "category", "path": "content_path", "size": "total_size",
        "ratio": "ratio", "seedingSeconds": "seeding_time", "completed": "completion_on",
    }.items()}


def completed_download(torrent, queued, policy):
    """Explain retention without treating completion/category as proof of import."""
    from media_policy import finished, seed_goal_reached, STOPPED, TRANSIENT
    if not finished(torrent):
        return None
    category = torrent.get("category")
    if category not in {"radarr", "sonarr", "prowlarr"}:
        return "Completed · unmanaged", "This category is not managed by Pompey. No automatic removal is scheduled. Review it in qBittorrent."
    if torrent.get("state") in TRANSIENT:
        return "Checking completed files", "qBittorrent is checking or moving the files. Removal waits for this to finish."
    if policy == "share_one_day" and "seeding_time" not in torrent:
        return "Seeding time unavailable", "The downloader snapshot does not include elapsed seeding time. Maintenance checks the native torrent properties before deciding whether to stop it."
    if not seed_goal_reached(torrent, policy):
        target = "a 1:1 upload ratio" if policy == "share_to_ratio" else "24 hours of seeding"
        return "Seeding target pending", f"Files are retained until {target}. Arr must also confirm import before removing the torrent."
    if torrent.get("state") not in STOPPED:
        return "Waiting to stop seeding", "The seeding target is met. Pompey stops this torrent on its next download check, normally within 30 seconds."
    if queued and queued.get("trackedDownloadState") == "imported":
        return "Imported · cleanup pending", "Arr reports this release imported and the torrent is stopped. Its next download check should remove the torrent and leftover download files."
    if queued:
        return "Waiting for import", "The torrent is complete and stopped. Arr has not confirmed import; files remain until import succeeds."
    if category == "prowlarr":
        return "Manual download retained", "Pompey submits matching manual downloads to Arr. Unmatched, rejected or ambiguous imports are retained; completion alone does not authorize removal."
    return "Completed · import unconfirmed", "This torrent is stopped but absent from the observed Arr queue. Import and removal eligibility are unconfirmed. Automatic maintenance continues. Review the native application if it remains; completion alone does not authorize deletion."


def workflow(sources, titles, queues, torrents, requested=()):
    """Explain Arr queues and missing monitored media, including non-Seerr work."""
    rows = []
    linked_hashes = set()
    policy = api.after_download()
    imports = state.load("download-imports")
    for kind in ("radarr", "sonarr"):
        source = sources.get(kind, {})
        native_titles = {row["id"]: row for row in source.get("data", {}).get("titles", [])}
        by_id = {row["id"]: row for row in titles if row["kind"] == kind}
        queue_by_title = {}
        for queued in queues.get(kind, []):
            ident = queued.get("movieId" if kind == "radarr" else "seriesId")
            queue_by_title.setdefault(ident, []).append(queued)
        for ident in dict.fromkeys([*by_id, *queue_by_title]):
            title = by_id.get(ident)
            native = native_titles.get(ident, {})
            owned = queue_by_title.get(ident, [])
            if not owned and title:
                if not title.get("monitored") and (kind, ident) not in requested:
                    continue
                if kind == "radarr" and (title.get("hasFile") or title.get("delivered")):
                    continue
                stats = native.get("statistics") or {}
                if kind == "sonarr" and all(type(stats.get(k)) is int for k in ("episodeCount", "episodeFileCount")) and stats["episodeCount"] <= stats["episodeFileCount"]:
                    continue  # No missing aired monitored episodes reported by Sonarr.
            for queued in owned or [None]:
                token = str((queued or {}).get("downloadId", "")).lower()
                torrent = torrents.get(token)
                if torrent:
                    linked_hashes.add(token)
                phase, action, messages = request_progress({"status": 2}, title, [queued] if queued else [], torrents)
                completion = completed_download(torrent, queued, policy) if torrent else None
                if completion and phase != "Import needs attention":
                    phase, action = completion
                if not queued and kind == "radarr" and title and not title.get("hasFile"):
                    if phase == "Waiting for a suitable release":
                        phase = "Not in Radarr library"
                    action = "Radarr reports no imported movie file in its library folder. If it already plays in Plex, check the folder below and use Check library. Plex availability does not establish that Radarr recognizes that file."
                if not queued and kind == "radarr" and native.get("isAvailable") is False:
                    phase, action = "Not available yet", "Radarr is waiting for the configured release availability."
                elif not queued and kind == "sonarr" and phase in {"Waiting for a suitable release", "Waiting for requested episodes"}:
                    stats = native.get("statistics") or {}
                    if all(type(stats.get(k)) is int for k in ("episodeCount", "episodeFileCount")):
                        missing = max(0, stats["episodeCount"] - stats["episodeFileCount"])
                        phase = "Missing episodes"
                        action = f"Sonarr reports {missing} missing aired, monitored episode(s) and {stats['episodeFileCount']} episode file(s). Existing episodes can be available in Plex while others are missing. Use Check library if files are already present."
                    else:
                        phase, action = "Episode status unavailable", "Sonarr did not report episode counts. Check its library before searching for another release."
                if not title:
                    phase, action = "Unmatched download", "Arr has not matched this download to a library title."
                observed = [source] + ([sources.get("qbittorrent", {})] if queued else [])
                available = all(item.get("available") for item in observed)
                episode = (queued or {}).get("episode") or {}
                season = episode.get("seasonNumber")
                number = episode.get("episodeNumber")
                scope = f"S{season:02d}E{number:02d}" if type(season) is int and type(number) is int else None
                rows.append({"kind": kind, "id": ident if title else None,
                    "key": f"{kind}:download:{token}:queue:{queued.get('id', '')}" if token else f"{kind}:queue:{queued['id']}" if queued else f"{kind}:title:{ident}",
                    "downloadId": token or None, "scope": scope,
                    "title": (title or {}).get("title") or (queued or {}).get("title", "Unmatched download"),
                    "release": (queued or {}).get("title"),
                    "path": (title or {}).get("path"), "canCheckLibrary": bool(title and not queued),
                    "phase": phase if available else "Current progress unavailable", "lastKnownPhase": phase,
                    "nextAction": action if available else "Restore the application connection before taking action.",
                    "messages": messages, "download": download_evidence(torrent) if torrent else None,
                    "available": available, "updated": min((item.get("updated") or 0 for item in observed), default=0),
                    "attention": bool(messages) or phase in {"Download stopped", "Download error", "Waiting for peers", "Unmatched download"}})
    for token, torrent in torrents.items():
        if token in linked_hashes:
            continue
        source = sources.get("qbittorrent", {})
        observed = [source]
        if torrent.get("category") in {"radarr", "sonarr"}:
            observed.append(sources.get(torrent["category"], {}))
        available = all(item.get("available") for item in observed)
        phase, action = completed_download(torrent, None, policy) or ("Outside Arr queue", "No matching entry is visible in Arr's queue. This can be a manual grab or pending synchronization; files are retained.")
        receipt = imports.get(token, {})
        managed = receipt.get("kind") in {"radarr", "sonarr"} and receipt.get("kind") == torrent.get("category")
        if managed:
            phase = "Import needs attention" if receipt.get("phase") in {"review", "submitting"} else "Waiting for import"
            action = ("The native import command has finished or its result is uncertain, but this download remains. Open Review import, choose Manual Import and select the download location shown below. Review file and episode matches before importing; completion alone does not authorize deletion."
                      if phase == "Import needs attention" else "Pompey submitted this complete, stopped download to Arr. Native import and cleanup are pending.")
        rows.append({"kind": receipt["kind"] if managed else "qbittorrent", "id": receipt.get("titleId") if managed else None, "title": torrent.get("name", "Download"),
            "manualImport": managed,
            "key": "qbittorrent:" + token, "downloadId": token,
            "phase": phase if available else "Current progress unavailable", "lastKnownPhase": phase,
            "nextAction": action,
            "messages": receipt.get("messages", []) if managed else [], "download": download_evidence(torrent), "available": available,
            "updated": min((item.get("updated") or 0 for item in observed), default=0), "attention": phase in {"Import needs attention", "Completed · import unconfirmed", "Manual download retained", "Completed · unmanaged"}})
    return sorted(rows, key=lambda row: (not row["attention"], not bool(row["download"]), row["title"].lower()))


def snapshot():
    from pompey_observations import snapshot as observations
    from request_policy import identity

    sources = observations(max_age=2)
    from pompey_delivery import identity as delivery_identity
    deliveries = state.load("deliveries").get("titles", {})
    result = {"updated": time.time(), "titles": [], "downloads": [], "requests": [], "attention": []}
    titles_by_identity = {}
    queues = {}
    torrents = {}
    for name, source in sources.items():
        if not source.get("available"):
            result["attention"].append(name + ": information unavailable" +
                ("; showing the last successful observation" if source.get("updated") else ""))
    for kind in ["radarr", "sonarr"]:
        data = sources.get(kind, {}).get("data", {})
        queue = data.get("queue", {})
        queues[kind] = queue.get("records", [])
        for row in data.get("titles", []):
            receipts = deliveries.get(delivery_identity(kind, row), {}).get("receipts", {})
            delivered = [r for r in receipts.values() if r.get("status") == "delivered"]
            title = {"kind": kind, "id": row["id"], "title": row.get("title", "Untitled"),
                     "monitored": row.get("monitored"), "path": row.get("path"),
                     "fileId": (row.get("movieFile") or {}).get("id") if kind == "radarr" else None,
                     "hasFile": row.get("hasFile") if kind == "radarr" else (row.get("statistics") or {}).get("episodeFileCount", 0),
                     "delivered": bool(delivered) if kind == "radarr" else len(delivered),
                     "deliveryPath": delivered[-1].get("path") if delivered else None,
                     "lastSearch": state.load("search-" + kind + "-" + str(row["id"])),
                     "available": sources[kind].get("available", False), "updated": sources[kind].get("updated")}
            result["titles"].append(title)
            titles_by_identity[("movie" if kind == "radarr" else "tv", str(row.get("tmdbId" if kind == "radarr" else "tvdbId")))] = title
        for row in queues[kind]:
            result["downloads"].append({"service": kind, "title": row.get("title"), "state": row.get("status"),
                "remainingBytes": row.get("sizeleft"), "importState": row.get("trackedDownloadState"),
                "messages": [str(text) for group in row.get("statusMessages", []) for text in group.get("messages", [])]})
        if queue.get("totalRecords", 0) > len(queues[kind]):
            result["attention"].append(kind + ": additional downloads are available in the native queue.")
    for row in sources.get("qbittorrent", {}).get("data", {}).get("torrents", []):
        torrents[str(row.get("hash", "")).lower()] = row
        result["downloads"].append({"service": "downloader", "title": row.get("name"), "state": row.get("state"),
            "category": row.get("category"), "progress": row.get("progress"), "connectedSeeds": row.get("num_seeds"), "path": row.get("content_path")})
    seerr = sources.get("seerr", {}).get("data", {})
    if seerr.get("initialized") is False:
        result["attention"].append("Complete Seerr setup to use requests. Library management remains available.")
    states = {1: "Awaiting approval", 2: "Approved", 3: "Declined", 4: "Failed", 5: "Completed"}
    for row in seerr.get("requests", []):
        title = titles_by_identity.get(identity(row))
        kind = title["kind"] if title else None
        owned_queue = [q for q in queues.get(kind, []) if q.get("movieId" if kind == "radarr" else "seriesId") == title["id"]] if title else []
        phase, action, messages = request_progress(row, title, owned_queue, torrents)
        expected_kind = "sonarr" if row.get("type") == "tv" else "radarr"
        required_sources = [sources.get("seerr", {})]
        if row.get("status") == 2:
            required_sources.append(sources.get(expected_kind, {}))
            if owned_queue:
                required_sources.append(sources.get("qbittorrent", {}))
        available = all(source.get("available", False) for source in required_sources)
        observed = min(source["updated"] for source in required_sources) if all(source.get("updated") for source in required_sources) else None
        result["requests"].append({"id": row.get("id"), "type": row.get("type"), "state": states.get(row.get("status"), "Unknown"),
            "title": title["title"] if title else ("Movie" if row.get("type") == "movie" else "TV") + " request #" + str(row["id"]),
            "arrLinked": bool(title), "arrId": title["id"] if title else None, "kind": kind,
            "phase": phase if available else "Current progress unavailable", "lastKnownPhase": phase if observed else None,
            "nextAction": action if available else "Check the application status and try again.", "messages": messages,
            "updated": observed, "available": available, "observedIn": ["Seerr"] + (["Radarr" if kind == "radarr" else "Sonarr"] if kind else []) + (["qBittorrent"] if owned_queue else [])})
    if seerr.get("total", 0) > len(result["requests"]):
        result["attention"].append("Showing the 100 most recent requests. Open Seerr for the full history.")
    requested = {(row["kind"], row["arrId"]) for row in result["requests"]
                 if row["state"] == "Approved" and row["arrLinked"]
                 and row["phase"] not in {"Available according to Seerr", "Imported to library", "Delivered"}}
    result["workflow"] = workflow(sources, result["titles"], queues, torrents, requested)
    for request in result["requests"]:
        if request["state"] == "Approved" and not request["arrLinked"]:
            result["workflow"].append({**request, "key": "seerr:request:" + str(request["id"]), "kind": "seerr", "id": None, "attention": True})
    from pompey_recovery import sequences
    result["sequences"] = sequences()
    result["requestReview"] = state.load("requests").get("reviewRequired", "")
    try:
        health = json.loads((Path(api.ready_dir()) / "health.json").read_text())
        job = health.get("jobs", {}).get("downloads", {})
        result["maintenance"] = {"updated": health.get("updated"), "lastSuccess": job.get("lastSuccess"),
            "running": job.get("running"), "error": job.get("error"), "policy": api.after_download()}
    except (OSError, ValueError):
        result["maintenance"] = {"policy": api.after_download()}
    return result


def check_downloads():
    from media_policy import maintain_downloads
    from pompey_runtime import stack_lock
    from pompey_recovery import api_for
    with stack_lock(blocking=False):
        require_storage()
        maintain_downloads()
        from pompey_delivery import reconcile
        reconcile()
        commands = {}
        for kind in ("radarr", "sonarr"):
            commands[kind] = api_for(kind)("POST", "/command", {"name": "RefreshMonitoredDownloads"})["id"]
    return {"ok": True, "commands": commands,
            "message": "Download policies checked. Radarr and Sonarr download checks submitted; import and cleanup continue in the background. Retained items show the remaining conditions below."}


def require_storage():
    path = Path(api.ready_dir()) / "storage.json"
    if not path.exists() or json.loads(path.read_text()).get("ok") is not True:
        raise ValueError("Storage must be healthy before checking downloads or rescanning the library")


def check_library(payload):
    from pompey_runtime import stack_lock
    from pompey_recovery import api_for
    kind, ident = payload.get("kind"), payload.get("id")
    if kind not in {"radarr", "sonarr"} or type(ident) is not int or ident <= 0:
        raise ValueError("Choose an existing movie or series")
    with stack_lock(blocking=False):
        require_storage()
        call = api_for(kind)
        call("GET", ("/movie/" if kind == "radarr" else "/series/") + str(ident))
        command = call("POST", "/command", {"name": "RescanMovie" if kind == "radarr" else "RescanSeries",
                       "movieId" if kind == "radarr" else "seriesId": ident})
    return {"ok": True, "commandId": command["id"], "message": "Library scan submitted. The application will check this title's configured folder for existing files. This does not search indexers or start a download."}


def explain_search(kind, ident):
    if kind not in {"radarr", "sonarr"} or type(ident) is not int or ident <= 0:
        raise ValueError("Choose an existing movie or series")
    secrets = api.load_secrets()
    key = secrets[kind + "_api_key"]
    host = api.radarr_url() if kind == "radarr" else api.sonarr_url()
    base = api.arr_api_root(host, key)
    collection = "movie" if kind == "radarr" else "series"
    api.http(
        "GET", base + "/" + collection + "/" + str(ident), headers=api.arr_headers(key)
    )
    name = "search-" + kind + "-" + str(ident)
    previous = state.load(name)
    if time.time() - previous.get("updated", 0) < 60:
        raise ValueError("Wait one minute before searching this title again")
    # Persist intent to bound repeated requests even if the upstream search times out.
    state.save(
        name,
        {"updated": time.time(), "status": "Searching; results are not yet available"},
    )
    try:
        rows = api.http(
            "GET",
            base + "/release?" + urlencode({collection + "Id": ident}),
            headers=api.arr_headers(key),
        )
        record = {
            "updated": time.time(),
            "status": "Search complete",
            "releases": [
                {
                    "title": r.get("title"),
                    "approved": r.get("approved"),
                    "rejections": r.get("rejections", []),
                    "reportedSeeders": r.get("seeders"),
                    "size": r.get("size"),
                    "score": r.get("customFormatScore"),
                }
                for r in api.object_list(rows, "Release search", ("title",))
            ],
        }
    except (RuntimeError, OSError, ValueError):
        state.save(
            name,
            {
                "updated": time.time(),
                "status": "Search failed or timed out. Release availability could not be determined.",
            },
        )
        raise
    state.save(name, record)
    return record


def rebaseline():
    import json
    from pathlib import Path
    from pompey_runtime import stack_lock

    with stack_lock(blocking=False):
        instance = json.loads(
            (Path(api.seerr_config_dir()) / "settings.json").read_text()
        ).get("clientId")
        if not instance:
            raise ValueError("Seerr setup is incomplete")
        state.save("requests", {"version": 3, "owned": {}, "instanceId": instance})
    return {
        "ok": True,
        "message": "Existing monitoring was preserved. Future request removals will be tracked from the current history.",
    }


if __name__ == "__main__":
    import json
    import sys

    try:
        action = sys.argv[1]
        if action == "snapshot":
            result = snapshot()
        elif action == "search":
            payload = json.load(sys.stdin)
            result = explain_search(payload.get("kind"), payload.get("id"))
        elif action == "rebaseline":
            result = rebaseline()
        elif action == "check-downloads":
            result = check_downloads()
        elif action == "check-library":
            result = check_library(json.load(sys.stdin))
        else:
            raise ValueError("Unknown diagnostic action")
        print(json.dumps(result))
    except BlockingIOError:
        print(json.dumps({"error": "Maintenance is running. Try again shortly."}))
        sys.exit(2)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
