#!/usr/bin/env python3
"""Gate native Arr scans on complete, stopped downloads; never move files here."""

import time
from pathlib import Path
from urllib.parse import urlencode
import pompey_common as api
import pompey_state as state

JOURNAL = "download-imports"


def endpoints(secrets):
    return [(kind, host + "/api/v3", api.arr_headers(secrets[kind + "_api_key"]))
            for kind, host in [("radarr", api.radarr_url()), ("sonarr", api.sonarr_url())]]


def disable_automatic_imports(secrets):
    for kind, base, headers in endpoints(secrets):
        url = base + "/config/downloadclient"
        cfg = api.http("GET", url, headers=headers, timeout=3)
        if not isinstance(cfg, dict) or "enableCompletedDownloadHandling" not in cfg:
            raise RuntimeError(kind + " did not report its automatic import setting")
        if cfg["enableCompletedDownloadHandling"] is not False:
            api.http("PUT", url + "/" + str(cfg.get("id", 1)),
                     {**cfg, "enableCompletedDownloadHandling": False}, headers=headers, timeout=3)
        verified = api.http("GET", url, headers=headers, timeout=3)
        if verified.get("enableCompletedDownloadHandling") is not False:
            raise RuntimeError(kind + " automatic importing is still enabled")


def before_downloader():
    """Keep restored torrents invisible until Arr's import gate is installed."""
    secrets = api.load_secrets()
    deadline = time.monotonic() + 90
    failure = "Arr is not ready"
    while time.monotonic() < deadline:
        try:
            from pompey_runtime import stack_lock
            with stack_lock(blocking=False):
                disable_automatic_imports(secrets)
            api.log("Automatic Arr importing disabled; starting downloader")
            return
        except (RuntimeError, BlockingIOError) as exc:
            failure = str(exc)
            time.sleep(1)
    raise RuntimeError("Downloader startup held: " + failure)


def library_availability(kind, base, headers, digest, title_id):
    """Observe the whole grabbed release; this evidence never authorizes deletion."""
    from pompey_delivery import final_path
    events = []
    history_size = None
    for page in range(1, 5):
        payload = api.http("GET", base + "/history?" + urlencode({
            "downloadId": digest.upper(), "page": page, "pageSize": 250}), headers=headers)
        if not isinstance(payload, dict):
            raise RuntimeError("Native import history is unavailable")
        rows = api.object_list(payload.get("records"), "import history", ("id",))
        total = payload.get("totalRecords")
        if type(total) is not int or total < 0:
            raise RuntimeError("Native import history did not report its size")
        if history_size is not None and total != history_size:
            raise RuntimeError("Native import history changed during inspection")
        history_size = total
        events.extend(rows)
        if len({row["id"] for row in events}) != len(events):
            raise RuntimeError("Native import history pages overlap")
        if len(events) >= total:
            break
        if not rows:
            raise RuntimeError("Native import history is incomplete")
    else:
        raise RuntimeError("Native import history exceeds the inspection limit")
    field = "movieId" if kind == "radarr" else "seriesId"
    grabs = [r for r in events if str(r.get("downloadId", "")).lower() == digest.lower()
             and r.get("eventType") == "grabbed"]
    if not grabs or any(r.get(field) != title_id for r in grabs):
        return {"state": "unknown", "detail": "Native history does not identify the complete release for this title."}
    title = api.http("GET", base + ("/movie/" if kind == "radarr" else "/series/") + str(title_id), headers=headers)
    if title.get("id") != title_id:
        raise RuntimeError("Native title identity changed")

    def present(file):
        path = file.get("path") or str(Path(title.get("path", "")) / file.get("relativePath", ""))
        if not file.get("id") or not final_path(path, kind) or type(file.get("size")) is not int or file["size"] <= 0:
            return False
        try:
            return Path(path).is_file() and Path(path).stat().st_size == file["size"]
        except OSError:
            return False

    if kind == "radarr":
        satisfied = bool(title.get("hasFile") and present(title.get("movieFile") or {}))
        missing = [] if satisfied else [title.get("title", "Movie")]
    else:
        expected = {r.get("episodeId") for r in grabs}
        if any(type(ident) is not int or ident <= 0 for ident in expected):
            return {"state": "unknown", "detail": "Native grab history does not identify every episode."}
        episodes = api.object_list(api.http("GET", base + "/episode?seriesId=" + str(title_id), headers=headers), "episodes", ("id",))
        files = api.object_list(api.http("GET", base + "/episodefile?seriesId=" + str(title_id), headers=headers), "episode files", ("id",))
        by_episode = {r["id"]: r for r in episodes if r.get("seriesId") == title_id}
        by_file = {r["id"]: r for r in files if r.get("seriesId") == title_id}
        missing = []
        for ident in sorted(expected):
            episode = by_episode.get(ident, {})
            if not episode.get("hasFile") or not present(by_file.get(episode.get("episodeFileId"), {})):
                season, number = episode.get("seasonNumber"), episode.get("episodeNumber")
                missing.append(f"S{season:02d}E{number:02d}" if type(season) is int and type(number) is int else "Episode ID " + str(ident))
        satisfied = not missing
    return {"state": "satisfied" if satisfied else "missing", "missing": missing}


def handoff(items, secrets):
    from media_policy import finished, contained
    # Also close the gate if a user changed it in a native UI. Never temporarily
    # enable it: that would allow unrelated seeding downloads to race this job.
    disable_automatic_imports(secrets)
    receipts = state.load(JOURNAL)
    present = {t.get("hash", "").lower() for t in items}
    receipts = {key: value for key, value in receipts.items()
                if key in present or value.get("phase") not in {"complete", "review"}}
    inspection_keys = [key for key, receipt in sorted(receipts.items(), key=lambda pair: pair[1].get("availabilityChecked", 0))
                       if key in present and receipt.get("phase") in {"review", "complete"} and receipt.get("titleId")
                       and time.time() - receipt.get("availabilityChecked", 0) >= 60][:4]
    for kind, base, headers in endpoints(secrets):
        for receipt_key, receipt in sorted(receipts.items(), key=lambda pair: pair[1].get("availabilityChecked", 0)):
            if receipt.get("kind") != kind:
                continue
            if receipt.get("phase") == "submitted":
                try:
                    result = api.http("GET", base + "/command/" + str(receipt["commandId"]), headers=headers)
                    if result.get("status") in {"completed", "failed", "aborted", "cancelled", "orphaned"}:
                        # A completed command is not proof of a successful
                        # whole-pack import. Native queue/history owns that.
                        receipt.update(phase="review", error="Review the native import result in " + kind + ".")
                        if Path(receipt["path"]).exists():
                            candidates = api.http("GET", base + "/manualimport?" + urlencode({
                                "folder": receipt["path"], "downloadId": receipt_key.upper(),
                                "filterExistingFiles": "false"}), headers=headers)
                            candidates = api.object_list(candidates, "native import inspection", ("path",))
                            receipt["messages"] = [str(reason.get("reason", reason)) if isinstance(reason, dict) else str(reason)
                                                   for row in candidates[:20] for reason in row.get("rejections", [])[:10]]
                except api.ApiError as exc:
                    if exc.status != 404:
                        raise
                    receipt.update(phase="review", error="Import command expired; review native history before retrying.")
            if receipt_key in inspection_keys:
                try:
                    receipt["availability"] = library_availability(kind, base, headers, receipt_key, receipt["titleId"])
                except (RuntimeError, OSError) as exc:
                    receipt["availability"] = {"state": "unknown", "detail": str(exc)}
                receipt["availabilityChecked"] = time.time()
        relevant = [t for t in items if t.get("category") == kind and t.get("hash")]
        if not relevant:
            continue
        commands = api.object_list(api.http("GET", base + "/command", headers=headers),
                                   "native commands", ("id", "name", "status"))
        moving = any(c["status"] in {"queued", "started"} and c["name"] in {
            "BulkMoveMovie", "BulkMoveSeries", "RefreshMovie", "RefreshSeries"} for c in commands)
        queue = api.http("GET", base + "/queue?page=1&pageSize=100&includeUnknownSeriesItems=false&includeUnknownMovieItems=false", headers=headers)
        rows = api.object_list(queue.get("records") if isinstance(queue, dict) else None, "import queue", ("id",))
        for torrent in relevant:
            key = torrent["hash"].lower()
            receipt = receipts.get(key)
            owned = [r for r in rows if str(r.get("downloadId", "")).lower() == key
                     and (r.get("movieId") if kind == "radarr" else r.get("seriesId"))]
            if not owned and finished(torrent) and torrent.get("state") in {"stoppedUP", "pausedUP"} and not receipt:
                # Disabled CDH can omit completed items from the queue. Require
                # the native grabbed history for this exact hash before handing
                # it back to the same application; category alone is not ownership.
                history = api.http("GET", base + "/history?downloadId=" + key.upper() + "&pageSize=100", headers=headers)
                events = api.object_list(history.get("records") if isinstance(history, dict) else None,
                                         "import history", ("id",))
                owned = [r for r in events if str(r.get("downloadId", "")).lower() == key
                         and r.get("eventType") == "grabbed"
                         and (r.get("movieId") if kind == "radarr" else r.get("seriesId"))]
            if receipt:
                if any(r.get("trackedDownloadState") == "imported" for r in owned):
                    receipt.update(phase="complete", error=None)
                continue
            if (not finished(torrent) or torrent.get("state") not in {"stoppedUP", "pausedUP"}
                    or moving or not owned or any(r.get("trackedDownloadState") == "imported" for r in owned)):
                continue
            path = torrent.get("content_path", "")
            if not contained(path, api.downloads_complete()) or not Path(path).exists():
                continue
            if len(receipts) >= 256:
                raise RuntimeError("Import handoff journal is full. Review unresolved imports before adding more downloads.")
            title_ids = {r.get("movieId" if kind == "radarr" else "seriesId") for r in owned}
            if len(title_ids) != 1:
                continue  # Never infer ownership of a cross-title download.
            receipt = {"kind": kind, "path": path, "title": torrent.get("name", key),
                       "titleId": title_ids.pop(), "phase": "submitting", "created": time.time()}
            receipts[key] = receipt
            # Commit intent before POST. A lost acknowledgement is never retried
            # automatically, including after a process/container restart.
            state.save(JOURNAL, receipts)
            command = api.http("POST", base + "/command", {
                "name": "DownloadedMoviesScan" if kind == "radarr" else "DownloadedEpisodesScan",
                "path": path, "downloadClientId": key.upper(), "importMode": "Move",
            }, headers=headers)
            if not isinstance(command, dict) or type(command.get("id")) is not int:
                raise RuntimeError("Native import submission returned no command identity; review " + kind)
            receipt.update(phase="submitted", commandId=command["id"])
            state.save(JOURNAL, receipts)
    receipts = {key: value for key, value in receipts.items()
                if key in present or value.get("phase") not in {"complete", "review"}}
    state.save(JOURNAL, receipts)


if __name__ == "__main__":
    before_downloader()
