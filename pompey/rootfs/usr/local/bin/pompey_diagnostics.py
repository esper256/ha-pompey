"""Diagnostic information from real service APIs; explicit searches never run on page load."""

import time
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
        return "Import needs attention", "Review the native queue messages below.", messages
    for row in queue:
        torrent = torrents.get(str(row.get("downloadId", "")).lower(), {})
        native = torrent.get("state", "")
        if native in {"stalledDL", "metaDL", "forcedMetaDL"}:
            return "Waiting for peers" if native == "stalledDL" else "Retrieving torrent metadata", "Review the downloader if this persists.", messages
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


def snapshot():
    from pompey_observations import snapshot as observations
    from request_policy import identity

    sources = observations(max_age=2)
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
            title = {"kind": kind, "id": row["id"], "title": row.get("title", "Untitled"),
                     "monitored": row.get("monitored"), "path": row.get("path"),
                     "hasFile": row.get("hasFile") if kind == "radarr" else (row.get("statistics") or {}).get("episodeFileCount", 0),
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
    result["requestReview"] = state.load("requests").get("reviewRequired", "")
    return result


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
        else:
            raise ValueError("Unknown diagnostic action")
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
