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


def handoff(items, secrets):
    from media_policy import finished, contained
    # Also close the gate if a user changed it in a native UI. Never temporarily
    # enable it: that would allow unrelated seeding downloads to race this job.
    disable_automatic_imports(secrets)
    receipts = state.load(JOURNAL)
    present = {t.get("hash", "").lower() for t in items}
    receipts = {key: value for key, value in receipts.items()
                if key in present or value.get("phase") not in {"complete", "review"}}
    for kind, base, headers in endpoints(secrets):
        for receipt_key, receipt in receipts.items():
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
        relevant = [t for t in items if t.get("category") == kind and t.get("hash")]
        if not relevant:
            continue
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
                    or not owned or any(r.get("trackedDownloadState") == "imported" for r in owned)):
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
