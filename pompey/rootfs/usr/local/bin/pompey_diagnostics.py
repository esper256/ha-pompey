"""Household evidence from real service APIs; explicit searches never run on page load."""

import time
from urllib.parse import urlencode
import pompey_common as api
import pompey_state as state


def snapshot():
    secrets = api.load_secrets()
    result = {"updated": time.time(), "titles": [], "downloads": [], "attention": []}
    title_names = {}
    for kind, host, collection in [
        ("radarr", api.radarr_url(), "movie"),
        ("sonarr", api.sonarr_url(), "series"),
    ]:
        key = secrets[kind + "_api_key"]
        base = api.arr_api_root(host, key)
        try:
            titles = api.object_list(
                api.http("GET", base + "/" + collection, headers=api.arr_headers(key)),
                "Arr titles",
                ("id",),
            )
            queue = api.http(
                "GET",
                base
                + "/queue?page=1&pageSize=100&includeUnknownSeriesItems=true&includeUnknownMovieItems=true",
                headers=api.arr_headers(key),
            )
            for title in titles:
                title_names[
                    (
                        "movie" if kind == "radarr" else "tv",
                        str(title.get("tmdbId" if kind == "radarr" else "tvdbId")),
                    )
                ] = title.get("title", "Untitled")
                result["titles"].append(
                    {
                        "kind": kind,
                        "id": title["id"],
                        "title": title.get("title", "Untitled"),
                        "monitored": title.get("monitored"),
                        "path": title.get("path"),
                        "hasFile": title.get("hasFile")
                        if kind == "radarr"
                        else (title.get("statistics") or {}).get("episodeFileCount", 0),
                        "lastSearch": state.load(
                            "search-" + kind + "-" + str(title["id"])
                        ),
                    }
                )
            for row in queue.get("records", []):
                result["downloads"].append(
                    {
                        "service": kind,
                        "title": row.get("title"),
                        "state": row.get("status"),
                        "remainingBytes": row.get("sizeleft"),
                        "importState": row.get("trackedDownloadState"),
                        "messages": [
                            message
                            for group in row.get("statusMessages", [])
                            for message in group.get("messages", [])
                        ],
                    }
                )
            if queue.get("totalRecords", 0) > len(queue.get("records", [])):
                result["attention"].append(
                    kind + ": more downloads are available in the native queue."
                )
        except (RuntimeError, OSError, ValueError) as exc:
            result["attention"].append(kind + ": evidence unavailable: " + str(exc))
    try:
        torrents = api.http("GET", api.qbit_url() + "/api/v2/torrents/info")
        for item in api.object_list(torrents, "Downloads", ("hash",)):
            result["downloads"].append(
                {
                    "service": "downloader",
                    "title": item.get("name"),
                    "state": item.get("state"),
                    "category": item.get("category"),
                    "progress": item.get("progress"),
                    "connectedSeeds": item.get("num_seeds"),
                    "path": item.get("content_path"),
                    "note": "A download need not be linked to a search request. Files are preserved.",
                }
            )
    except (RuntimeError, OSError, ValueError) as exc:
        result["attention"].append("Downloader evidence unavailable: " + str(exc))
    try:
        from request_policy import requests_snapshot, identity

        states = {
            1: "Awaiting approval",
            2: "Approved",
            3: "Declined",
            4: "Failed",
            5: "Completed",
        }
        result["requests"] = [
            {
                "id": r.get("id"),
                "type": r.get("type"),
                "state": states.get(r.get("status"), "Unknown state"),
                "title": title_names.get(
                    identity(r),
                    "Movie request" if r.get("type") == "movie" else "TV request",
                ),
                "arrLinked": identity(r) in title_names,
            }
            for r in requests_snapshot()
        ]
    except (RuntimeError, OSError, ValueError) as exc:
        result["attention"].append("Search request evidence unavailable: " + str(exc))
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
        raise ValueError("Please wait one minute before searching this title again")
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
                "status": "Search failed or timed out. This does not mean no releases exist.",
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
            raise ValueError("Search setup is not complete")
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
