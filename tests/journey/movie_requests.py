"""Movie application workflows; transport/setup/reporting live in shared helpers."""

from pathlib import Path
import json
import os
import time
import urllib.parse
from applications import tunnel_bytes
from stack_checks import outage
from support import download_file_identity, assert_native_move, digest, save_json, HttpFailure
from request_lifecycle import close_request


def allowed_qualities(profile):
    result = set()

    def visit(items, parent=True):
        for item in items:
            allowed = parent and item["allowed"]
            if allowed and item.get("quality"):
                result.add(item["quality"]["id"])
            visit(item.get("items") or [], allowed)

    visit(profile["items"])
    return result


def movie_request(client, case, background, ledger_file, *, resume=False):
    if resume:
        client.evidence.active = next(
            c for c in client.evidence.data["cases"] if c["prefix"] == case.prefix
        )
        client.evidence.active.update(status="running", resumed=True)
    else:
        client.evidence.begin(case)
    if case.late_certification:
        client.service("wire", False)
    try:
        return _movie_request(client, case, background, ledger_file, resume=resume)
    finally:
        if case.late_certification:
            client.service("wire", True)


def _movie_request(client, case, background, ledger_file, *, resume):
    ledger = background.ledger
    evidence = client.evidence
    active = evidence.active
    wait = client.wait
    stamp = evidence.stamp
    print(
        "CASE",
        case.prefix,
        "tmdbId",
        case.movie_id,
        "certification",
        repr(case.certification),
        "priorTitles",
        len(ledger),
        flush=True,
    )
    before = tunnel_bytes()
    download = case
    status = client.peer(case)
    assert resume or status["blocks"] == 0, (
        "Unrequested case already downloaded",
        case.prefix,
        status,
    )
    infohash = status["infoHash"]
    active["infoHash"] = infohash

    def torrent():
        rows = client.qbit("torrents/info?hashes=" + infohash)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def movie():
        return next(
            (m for m in client.arr("GET", "/movie") if m["tmdbId"] == case.movie_id),
            None,
        )

    if resume:
        request = client.search("GET", "/request/" + str(active["requestId"]))
        assert (
            request["id"] == active["requestId"]
            and request["media"]["tmdbId"] == case.movie_id
        ), request
    else:
        results = client.search(
            "GET", "/search?query=" + urllib.parse.quote(case.title)
        )
        assert any(row.get("id") == case.movie_id for row in results["results"]), (
            results
        )
        stamp("catalogue-searched")
        options = {"mediaType": "movie", "mediaId": case.movie_id}
        if case.scenario == "anything-480p":
            profiles = client.arr("GET", "/qualityprofile")
            options["profileId"] = next(
                p["id"] for p in profiles if p["name"] == "Anything"
            )
        request = client.search("POST", "/request", options)
        assert request.get("id"), request
        active["requestId"] = request["id"]
        stamp("requested")
    linked = wait(movie, "Seerr did not add the movie")
    profiles = client.arr("GET", "/qualityprofile")
    chosen = next(p for p in profiles if p["id"] == linked["qualityProfileId"])
    assert chosen["name"] == (
        "Anything" if case.scenario == "anything-480p" else "Default"
    ), chosen
    if case.scenario == "waiting-content":
        waiting = wait(lambda: movie(), "Waiting request was not monitored")
        command = client.arr(
            "POST", "/command", {"name": "MoviesSearch", "movieIds": [waiting["id"]]}
        )
        client.wait_command(command["id"])
        assert waiting["monitored"] and not waiting["hasFile"]
        assert client.peer(case)["blocks"] == 0 and not torrent()
        rss = client.arr("POST", "/command", {"name": "RssSync"})
        client.wait_command(rss["id"])
        if evidence.data.get("willRestart") and not resume:
            active.update(resumeStage="waiting", savedArrId=waiting["id"])
            return "waiting"
        client.session.call(
            "POST", f"http://10.77.0.2:18080/control/publish/{case.movie_id}", {}
        )
        rss = client.arr("POST", "/command", {"name": "RssSync"})
        client.wait_command(rss["id"])
        active["waitingThenRss"] = True
    wait(
        lambda: client.peer(case)["blocks"] > 0,
        "Request did not produce a real torrent block",
        90,
    )
    stamp("first-peer-block")
    current = torrent()
    assert current and current["progress"] < 1, (case.prefix, current)
    client.refresh_downloads()
    item = movie()
    assert item and not item["hasFile"], item
    parsed = client.arr("GET", "/parse?title=" + urllib.parse.quote(case.release))
    assert (parsed.get("movie") or {}).get("id") == item["id"] and case.title in parsed["parsedMovieInfo"]["movieTitles"], (
        "Fixture title did not survive native parsing; ID-only matching is not import confidence", parsed)
    if case.scenario == "movie-pg":
        choices = client.arr("GET", "/release?movieId=" + str(item["id"]))
        low = [
            r
            for r in choices
            if r["title"].startswith(case.prefix + ".") and ".480p." in r["title"]
        ]
        assert low and all(
            r.get("rejections")
            and r["quality"]["quality"]["id"] not in allowed_qualities(chosen)
            for r in low
        ), (
            "Default accepted low quality",
            low,
        )
        active["defaultQualityRejections"] = low
    stamp("incomplete-preserved")
    if case.scenario == "restart-partial" and not resume:
        partial = wait(
            lambda: (t := torrent()) and 0 < t["progress"] < 1 and t,
            "No partial download to preserve",
        )
        active.update(
            resumeStage="partial",
            savedArrId=item["id"],
            partialProgress=partial["progress"],
            completedBytes=partial["completed"],
            sentBeforeRestart=client.peer(case)["sent"],
            tunnelBytesBeforeRestart=tunnel_bytes() - before,
        )
        return "restart"
    if case.scenario == "restart-partial" and resume:
        current = torrent()
        assert (
            item["id"] == active["savedArrId"]
            and current
            and 0 < current["progress"] < 1
        ), current
        assert current["completed"] >= active["completedBytes"], (
            "Partial bytes were lost",
            active,
            current,
        )
        source_files = [
            p for p in Path(current["content_path"]).rglob("*") if p.is_file()
        ]
        assert source_files, "Partial files disappeared at restart"
        checks = {str(p): digest(p) for p in source_files}
        state = close_request(client, {"arrId": item["id"], "requestId": request["id"]})
        assert torrent() and torrent()["progress"] < 1, (
            "Closing a request removed the active torrent"
        )
        assert all(
            Path(path).is_file() and digest(path) == checksum
            for path, checksum in checks.items()
        ), "Closing a request changed partial files"
        active["requestState"] = state
        active["restartPreservedPartial"] = True
    if case.scenario == "movie-r" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        from release_recovery import reject_three_releases
        download, infohash = reject_three_releases(client, case, item)
        active["infoHash"] = infohash
        active["selectedRelease"] = download.release
        active["releaseRecoveryVerified"] = True
    received_before_incident = 0
    if case.scenario == "vpn-download-loss":
        from catalogue import cases, offers
        # The unselected low-quality alternative has its own hash, so this
        # stopped control cannot collide with an earlier imported owner or the
        # configured clone's next requested title.
        control = offers(cases()[0])[1]
        paused_hash = client.peer(control)["infoHash"]
        client.qbit("torrents/createCategory", {"category": "pompey-test-paused"})
        client.qbit("torrents/add", {"urls": f"http://10.77.0.2:18080/download/{control.movie_id}", "category": "pompey-test-paused", "stopped": "true"})
        def stopped_control():
            rows = client.qbit("torrents/info?hashes=" + paused_hash)
            return rows and rows[0]["state"] in {"stoppedDL", "pausedDL"} and rows[0]
        paused = wait(stopped_control, "Control torrent did not settle into its requested stopped state", 10)
        partial_bytes = current["completed"]
        received_before_incident = tunnel_bytes() - before
        outage(client)
        preserved = wait(lambda: (rows := client.qbit("torrents/info?hashes=" + infohash)) and rows[0]["completed"] >= partial_bytes and rows[0], "Partial bytes were not retained through downloader reconnection")
        paused = wait(stopped_control, "Reconnection did not retain the user-stopped torrent", 10)
        assert paused["state"] in {"stoppedDL", "pausedDL"} and paused["downloaded"] == 0, paused
        active["pausedControlPreserved"] = True
        active["partialBytesRetained"] = preserved["completed"]
        active["vpnLossDuringDownload"] = True
        assert not movie()["hasFile"], (
            "Incomplete movie imported during VPN interruption"
        )
        before = tunnel_bytes()
    if case.unwanted_files:
        entries = client.qbit("torrents/files?hash=" + infohash)
        unwanted = [f for f in entries if Path(f["name"]).name in case.unwanted_files]
        assert {Path(f["name"]).name for f in unwanted} == set(case.unwanted_files), (
            "Unwanted files were not included in the real torrent",
            entries,
        )
        assert all(f["priority"] > 0 for f in unwanted), (
            "Unwanted files were skipped instead of downloaded",
            unwanted,
        )
        active["unwantedFilesIncluded"] = unwanted
    move_identity = None
    if case.scenario == "movie-pg" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        # Reproduce a persisted/indexer-specific goal while the fixture gate
        # keeps the download incomplete. Only product maintenance may reset it.
        client.qbit("torrents/setShareLimits", {"hashes": infohash, "ratioLimit": 1,
                                                "seedingTimeLimit": 1440,
                                                "inactiveSeedingTimeLimit": -1,
                                                "shareLimitAction": "Stop", "shareLimitsMode": "MatchAny"})
        reconciled = wait(lambda: (t := torrent()) and t.get("ratio_limit") == 0
                          and t.get("seeding_time_limit") == -1 and t,
                          "Managed torrent retained an incompatible seeding goal")
        active["shareLimitsReconciled"] = True
        move_identity = download_file_identity(reconciled)
        settings = client.arr("GET", "/config/mediamanagement")
        assert settings["copyUsingHardlinks"] is False, settings
    client.release(download)
    wait(
        lambda: (t := torrent()) and t["progress"] == 1,
        "Downloader did not verify the payload",
    )
    if move_identity:
        # Exercise the native move precondition before explicitly requesting
        # an immediate Arr scan. Do not stop/resume the torrent from the test.
        wait(lambda: (t := torrent()) and t["state"] in {"stoppedUP", "pausedUP"},
               "Completed download did not stop for native move import")
    stamp("download-verified")
    client.refresh_downloads()
    item = wait(
        lambda: (m := movie()) and m["hasFile"] and m, "Arr did not import the movie"
    )
    profiles = client.arr("GET", "/qualityprofile")
    profile = next(p for p in profiles if p["id"] == item["qualityProfileId"])
    assert profile["name"] == (
        "Anything" if case.scenario == "anything-480p" else "Default"
    ), profile
    if case.scenario == "anything-480p":
        assert profile["upgradeAllowed"] is False
    active["qualityProfile"] = profile["name"]
    if case.scenario == "anything-480p":
        native = client.arr("GET", "/moviefile/" + str(item["movieFile"]["id"]))
        assert (
            native["mediaInfo"]["resolution"] == "854x480"
            and native["quality"]["quality"]["resolution"] == 480
        ), native
        active["nativeMediaInfo"] = native["mediaInfo"]
    if active.get("requestState") == "deleted":
        assert not item["monitored"], item
    stamp("imported")
    if case.late_certification:
        staging = Path(item["path"])
        assert str(staging).startswith("/media/dlna/downloads/By Rating/Movies/"), item
        staged = next(staging.glob("*.mkv"))
        before_move = digest(staged)
        client.session.call(
            "POST", f"http://10.77.0.2:18080/control/certify/{case.movie_id}", {}
        )
        command = client.arr(
            "POST", "/command", {"name": "RefreshMovie", "movieId": item["id"]}
        )
        client.wait_command(command["id"])
        assert client.arr("GET", "/movie/" + str(item["id"]))["certification"] == "PG"
        active["freshHealthSince"] = time.time()
        client.service("wire", True)

        def move_intent():
            state = Path("/data/pompey/state/routing-movie.json")
            if not state.exists():
                return False
            return (
                json.loads(state.read_text())
                .get(str(item["id"]), {})
                .get("pending", {})
                .get("hadFiles")
            )

        wait(move_intent, "Routing did not record a move with existing files")
        active["moveWithFiles"] = {
            "stagingPath": str(staging),
            "sha256": before_move,
            "hadFiles": True,
        }

    base = Path("/media/dlna/Movies")

    def library_file():
        matches = [
            p
            for p in (base / case.library).rglob("*.mkv")
            if p.name.startswith(case.prefix)
        ]
        assert len(matches) <= 1, (case.prefix, matches)
        return matches[0] if matches else None

    imported = wait(
        library_file,
        "Controller did not route " + case.prefix + " to " + case.library,
        90,
    )
    if move_identity:
        assert_native_move(move_identity, imported)
        active["nativeMoveVerified"] = move_identity
    stamp("library-routed")
    if case.late_certification:
        assert digest(imported) == active["moveWithFiles"]["sha256"]
        assert not list(Path(active["moveWithFiles"]["stagingPath"]).rglob("*.*")), (
            "Staging payload remains"
        )

    source = Path("/tmp/world") / download.release / (download.release + ".mkv")
    checksum = digest(imported)
    assert checksum == digest(source), "Imported bytes belong to another case"
    wrong = base / (
        "Not Kid Friendly" if case.library == "Kid Friendly" else "Kid Friendly"
    )
    assert not [p for p in wrong.rglob("*.mkv") if p.name.startswith(case.prefix)], (
        "Wrong library contains this case"
    )
    subtitles = list(imported.parent.glob("*.srt"))
    assert len(subtitles) == 1 and digest(subtitles[0]) == digest(
        source.with_suffix(".en.srt")
    ), "Wrong or missing subtitle"
    transfer = client.peer(download)
    assert (
        transfer["sent"] + active.get("sentBeforeRestart", 0)
        >= transfer["payloadBytes"]
    ), "Payload not transferred from real peer"
    assert (
        tunnel_bytes()
        - before
        + received_before_incident
        + active.get("tunnelBytesBeforeRestart", 0)
        >= transfer["payloadBytes"]
    ), "Payload bypassed tunnel"
    diagnosis = client.session.call("GET", "http://127.0.0.1:8099/diagnostics")
    if active.get("requestState") == "deleted":
        assert not any(r["id"] == request["id"] for r in diagnosis["requests"]), (
            diagnosis
        )
    else:
        assert any(
            r["id"] == request["id"] and r["arrLinked"] for r in diagnosis["requests"]
        ), diagnosis
    assert any(
        t["id"] == item["id"] and t["title"] == case.title and t["hasFile"]
        for t in diagnosis["titles"]
    ), diagnosis
    if os.environ.get("POMPEY_TEST_BASELINE") != "1":
        overview = client.session.call("GET", "http://127.0.0.1:8099/diagnostics/overview")
        assert all(row["available"] for row in overview["summary"].values()), overview
        assert overview["summary"]["indexers"]["value"] == {"healthy": 1, "enabled": 1}, overview
        assert overview["setup"]["initialized"] is True, overview
        assert overview["setup"]["sources"] == {"radarr": True, "sonarr": True}, overview
        assert {"Radarr", "Sonarr", "Prowlarr", "seerr", "qbittorrent-nox"} <= overview["versions"]["applications"].keys(), overview
        assert not any(secret and secret in json.dumps(overview) for name, secret in client.secrets.items() if "key" in name.lower() or "password" in name.lower()), "Dashboard disclosed credentials"
        active["overviewVerified"] = overview["summary"]
    explanation = client.session.call(
        "POST",
        "http://127.0.0.1:8099/diagnostics/search",
        {"kind": "radarr", "id": item["id"]},
    )
    assert explanation["status"] == "Search complete" and any(
        r["title"] == download.release and "rejections" in r
        for r in explanation["releases"]
    ), explanation
    if case.scenario == "movie-pg" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        def remember_host():
            try:
                result = client.session.call("POST", "http://127.0.0.1:8099/diagnostics/access-host", {"hostname": "ha.pompey-test.invalid"})
                if not result.get("ok"):
                    return False
                settings = client.session.call("GET", "http://127.0.0.1:9696/api/v1/config/host", key=client.secrets["prowlarr_api_key"])
                warnings = client.session.call("GET", "http://127.0.0.1:9696/api/v1/health", key=client.secrets["prowlarr_api_key"])
                return settings if "ha.pompey-test.invalid" in settings["allowedHosts"] and not any("AllowedHosts" in row.get("source", "") for row in warnings) else False
            except HttpFailure as exc:
                if exc.status != 409:
                    raise
                return False
        hosts = client.wait(remember_host, "Ingress hostname was not applied to Prowlarr", 10)
        assert "ha.pompey-test.invalid" in hosts["allowedHosts"] and "*" not in hosts["allowedHosts"], hosts["allowedHosts"]
        exported = client.session.call("POST", "http://127.0.0.1:8099/diagnostics/report", {})
        report = json.loads(exported.split("\n\n", 1)[1])
        assert report["schema"] == "pompey-release-sequences-v1", report
        assert "logs" not in report and "applications" not in report, "General logs must remain in HA Logs"
        active["developmentReportVerified"] = True
    if active.get("releaseRecoveryVerified"):
        from release_recovery import assert_sequence
        assert_sequence(client, item["id"], download.release)
    # Both the released 0.6.1 baseline and the candidate stop monitoring
    # delivered movies. Snapshot only after that normal transition completes.
    def delivered():
        data = client.session.call("GET", "http://127.0.0.1:8099/diagnostics")
        return any(t["kind"] == "radarr" and t["id"] == item["id"] and t.get("delivered") for t in data["titles"]) and not movie()["monitored"]
    wait(delivered, "Final library import did not record delivery and stop monitoring")
    item = movie()
    if case.scenario == "movie-pg" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        from release_recovery import post
        checked = post(client, "/check-downloads", {})
        for kind, command in checked["commands"].items():
            client.wait_command(command, kind)
        wait(lambda: not torrent(), "Native cleanup did not remove the delivered torrent")
        moved = wrong / imported.parent.name
        original_folder = imported.parent
        original_folder.rename(moved)
        # Leave the original title directory, as a file move does. Radarr
        # intentionally skips missing-file reconciliation for an empty root.
        original_folder.mkdir()
        imported = moved / imported.name
        subtitles = [moved / p.name for p in subtitles]
        scan = post(client, "/check-library", {"kind":"radarr","id":item["id"]})
        client.wait_command(scan["commandId"])
        wait(lambda: not movie()["hasFile"], "Radarr did not notice the user-moved file")
        wait(delivered, "Moving the file reopened its delivery")
        def moved_delivery_visible():
            current = client.session.call("GET", "http://127.0.0.1:8099/diagnostics")
            title = next((t for t in current["titles"] if t["kind"] == "radarr" and t["id"] == item["id"]), {})
            pending = [r for r in current["workflow"] if r["kind"] == "radarr" and r["id"] == item["id"]]
            active["movedDeliveryObservation"] = {"title": title, "pending": pending, "attention": current["attention"]}
            # Native cleanup/rescan and the dashboard's cached projection
            # converge independently. Require the complete visible outcome,
            # with healthy sources, rather than inspecting the old cache.
            return (not current["attention"] and title.get("available") and title.get("delivered")
                    and title.get("hasFile") is False and not pending)
        wait(moved_delivery_visible, "Dashboard did not show the moved movie as delivered after cleanup", 10)
        choices = post(client, "/recovery/releases", {"kind":"radarr","id":item["id"]})
        assert choices["choices"] and choices["choices"][0].get("movedFilesRetained"), choices
        plan = post(client, "/recovery/preview", {"kind":"radarr","id":item["id"],"downloadId":infohash})
        assert not plan["libraryFiles"] and digest(imported) == checksum, plan
        active["manualMovePreservedDelivery"] = True
    assert not client.peer(download)["unknown"], client.peer(download)["unknown"]
    ledger.append(
        {
            "prefix": case.prefix,
            "tmdbId": case.movie_id,
            "arrId": item["id"],
            "requestId": request["id"],
            "monitored": item["monitored"],
            "manuallyMoved": active.get("manualMovePreservedDelivery", False),
            "since": active["wallStarted"],
            "infoHash": infohash,
            "kind": "radarr",
            "scenario": case.scenario,
            "requestState": active.get("requestState", "active"),
            "cleanupDeadline": time.time() + 60,
            "path": str(imported),
            "sha256": checksum,
            "subtitle": str(subtitles[0]),
            "subtitleSha256": digest(subtitles[0]),
            "unwantedFiles": list(case.unwanted_files),
        }
    )
    save_json(ledger_file, ledger)
    active.update(
        payloadBytes=transfer["payloadBytes"],
        transferredBytes=transfer["sent"],
        externalTimings=transfer["timings"],
        libraryPath=str(imported),
        sha256=checksum,
        priorTitlesVerified=len({r["prefix"] for r in ledger}) - 1,
    )

    active["cleanupDeadline"] = ledger[-1]["cleanupDeadline"]
