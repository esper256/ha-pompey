"""Movie household outcomes; transport/setup/reporting live in shared helpers."""

from pathlib import Path
import urllib.parse
from household import tunnel_bytes
from support import digest, save_json


def movie_request(client, case, background, ledger_file):
    ledger = background.ledger
    evidence = client.evidence
    evidence.begin(case)
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
    background.verify(client)
    before = tunnel_bytes()
    status = client.peer(case)
    assert status["blocks"] == 0, (
        "Unrequested case already downloaded",
        case.prefix,
        status,
    )
    infohash = status["infoHash"]

    def torrent():
        rows = client.qbit("torrents/info?hashes=" + infohash)
        assert len(rows) <= 1, rows
        return rows[0] if rows else None

    def movie():
        return next(
            (m for m in client.arr("GET", "/movie") if m["tmdbId"] == case.movie_id),
            None,
        )

    results = client.search("GET", "/search?query=" + urllib.parse.quote(case.title))
    assert any(row.get("id") == case.movie_id for row in results["results"]), results
    stamp("catalogue-searched")
    request = client.search(
        "POST", "/request", {"mediaType": "movie", "mediaId": case.movie_id}
    )
    assert request.get("id"), request
    stamp("requested")
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
    stamp("incomplete-preserved")
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
    stamp("imported")
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
    assert transfer["sent"] >= transfer["payloadBytes"], (
        "Payload not transferred from real peer"
    )
    assert tunnel_bytes() - before >= transfer["payloadBytes"], (
        "Payload bypassed tunnel"
    )
    diagnosis = client.session.call("GET", "http://127.0.0.1:8097/diagnostics")
    assert any(
        r["id"] == request["id"] and r["arrLinked"] for r in diagnosis["requests"]
    ), diagnosis
    assert any(
        t["id"] == item["id"] and t["title"] == case.title and t["hasFile"]
        for t in diagnosis["titles"]
    ), diagnosis
    explanation = client.session.call(
        "POST",
        "http://127.0.0.1:8097/diagnostics/search",
        {"kind": "radarr", "id": item["id"]},
    )
    assert explanation["status"] == "Search complete" and any(
        r["title"] == case.release and "rejections" in r
        for r in explanation["releases"]
    ), explanation
    assert not client.peer(case)["unknown"], client.peer(case)["unknown"]
    background.verify(client)
    ledger.append(
        {
            "prefix": case.prefix,
            "tmdbId": case.movie_id,
            "arrId": item["id"],
            "requestId": request["id"],
            "path": str(imported),
            "sha256": checksum,
            "subtitle": str(subtitles[0]),
            "subtitleSha256": digest(subtitles[0]),
        }
    )
    save_json(ledger_file, ledger)
    active.update(
        payloadBytes=transfer["payloadBytes"],
        transferredBytes=transfer["sent"],
        externalTimings=transfer["timings"],
        libraryPath=str(imported),
        sha256=checksum,
        priorTitlesVerified=len(ledger) - 1,
    )
    evidence.finish()
