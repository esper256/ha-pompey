"""Movie application workflows; transport/setup/reporting live in shared helpers."""

from pathlib import Path
import json
import time
import urllib.parse
from applications import tunnel_bytes
from stack_checks import outage
from support import digest, save_json
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
    received_before_incident = 0
    if case.scenario == "vpn-download-loss":
        received_before_incident = tunnel_bytes() - before
        outage(client)
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
    client.release(case)
    wait(
        lambda: (t := torrent()) and t["progress"] == 1,
        "Downloader did not verify the payload",
    )
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
    assert item["monitored"] is (active.get("requestState") != "deleted"), item
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
    stamp("library-routed")
    if case.late_certification:
        assert digest(imported) == active["moveWithFiles"]["sha256"]
        assert not list(Path(active["moveWithFiles"]["stagingPath"]).rglob("*.*")), (
            "Staging payload remains"
        )

    source = Path("/tmp/world") / case.release / (case.release + ".mkv")
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
    transfer = client.peer(case)
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
    explanation = client.session.call(
        "POST",
        "http://127.0.0.1:8099/diagnostics/search",
        {"kind": "radarr", "id": item["id"]},
    )
    assert explanation["status"] == "Search complete" and any(
        r["title"] == case.release and "rejections" in r
        for r in explanation["releases"]
    ), explanation
    assert not client.peer(case)["unknown"], client.peer(case)["unknown"]
    ledger.append(
        {
            "prefix": case.prefix,
            "tmdbId": case.movie_id,
            "arrId": item["id"],
            "requestId": request["id"],
            "monitored": item["monitored"],
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
