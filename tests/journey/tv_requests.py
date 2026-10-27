"""TV integration scenarios through real Seerr, Sonarr, source and downloader."""

from pathlib import Path
import os
import time
import urllib.parse
from catalogue import offers
from applications import tunnel_bytes
from support import digest, save_json


def tv_request(client, case, background, ledger_file):
    evidence = client.evidence
    evidence.begin(case)
    active = evidence.active
    active["kind"] = "sonarr"
    api = client.sonarr
    before = tunnel_bytes()
    found = client.search("GET", "/search?query=" + urllib.parse.quote(case.title))
    assert any(
        r.get("id") == case.movie_id and r.get("mediaType") == "tv"
        for r in found["results"]
    ), found
    request = client.search(
        "POST",
        "/request",
        {
            "mediaType": "tv",
            "mediaId": case.movie_id,
            "seasons": case.requested_seasons,
        },
    )
    active["requestId"] = request["id"]
    evidence.stamp("requested")
    series = client.wait(
        lambda: next(
            (s for s in api("GET", "/series") if s["tvdbId"] == case.tvdb_id), None
        ),
        "Seerr did not add the requested series",
    )
    active["arrId"] = series["id"]
    native_episodes = client.wait(
        lambda: api("GET", "/episode?seriesId=" + str(series["id"])),
        "Sonarr did not load episode metadata",
    )
    assert {(e["seasonNumber"], e["episodeNumber"]) for e in native_episodes} == {
        (s, n) for s in case.seasons for n in range(1, case.episode_count + 1)
    }, native_episodes
    actual_seasons = {
        s["seasonNumber"]: s["monitored"]
        for s in series["seasons"]
        if s["seasonNumber"] > 0
    }
    assert actual_seasons == {s: s in case.requested_seasons for s in case.seasons}, (
        actual_seasons
    )
    profiles = api("GET", "/qualityprofile")
    profile = next(p for p in profiles if p["id"] == series["qualityProfileId"])
    assert profile["name"] == ("Anime" if case.anime else "Default"), {
        "expected": "Anime" if case.anime else "Default",
        "actual": profile["name"],
    }
    decisions = [
        r
        for r in api("GET", "/release?seriesId=" + str(series["id"]))
        if r["title"].startswith(case.prefix + ".")
    ]
    if case.scenario == "single-episode":
        foreign = [r for r in decisions if r["title"].endswith(".GERMAN")]
        assert foreign and all(
            r.get("rejections") and r["customFormatScore"] < profile["minFormatScore"]
            for r in foreign
        ), foreign
        original = [r for r in decisions if ".ENGLISH" in r["title"]]
        assert original and all(
            r["customFormatScore"] >= profile["minFormatScore"] for r in original
        ), original
        active["originalLanguageProtection"] = {"foreignRejections": foreign}
    if case.anime:
        scores = {r["title"]: r["customFormatScore"] for r in decisions}
        assert (
            scores[case.release]
            > scores[case.prefix + ".S01.1080p.WEB-DL.JAPANESE"]
            > scores[case.prefix + ".S01E01.1080p.WEB-DL.JAPANESE"]
        ), scores
        active["animePreference"] = {
            "preferred": case.release,
            "decisions": decisions,
        }
    candidates = offers(case)

    def grabbed():
        for offer in candidates:
            if client.peer(offer)["blocks"] > 0:
                return offer
        return None

    selected = client.wait(
        grabbed, "Requested TV release did not reach the real peer", 90
    )
    assert selected.season in case.requested_seasons, selected
    assert selected.language != "foreign", selected
    if case.anime:
        assert selected.variant == 0, (
            "Anime did not prefer the dual-audio season pack",
            selected,
        )
    status = client.peer(selected)
    infohash = status["infoHash"]
    active["infoHash"] = infohash
    active["selectedRelease"] = selected.release

    def torrent():
        rows = client.qbit("torrents/info?hashes=" + infohash)
        return rows[0] if rows else None

    current = torrent()
    assert current and current["progress"] < 1, current
    command = api("POST", "/command", {"name": "RefreshMonitoredDownloads"})
    client.wait_command(command["id"], "sonarr")
    assert not api("GET", "/episodefile?seriesId=" + str(series["id"])), (
        "Incomplete episodes imported"
    )
    evidence.stamp("incomplete-preserved")
    if case.scenario == "single-episode" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        def workflow_episode():
            diagnostic = client.session.call("GET", "http://127.0.0.1:8099/diagnostics")
            return [row for row in diagnostic["workflow"]
                    if row.get("downloadId") == infohash.lower() and row.get("scope")]

        workflow_rows = client.wait(workflow_episode, "Ingress did not identify the native TV episode", 10)
        assert {row["scope"] for row in workflow_rows} == {
            f"S{selected.season:02d}E{episode:02d}" for episode in selected.episodes
        }, workflow_rows
        assert len({row["key"] for row in workflow_rows}) == len(workflow_rows), workflow_rows
        active["workflowEpisodeVerified"] = True
    client.release(selected)
    client.wait(
        lambda: (t := torrent()) and t["progress"] == 1, "TV payload was not verified"
    )
    command = api("POST", "/command", {"name": "RefreshMonitoredDownloads"})
    client.wait_command(command["id"], "sonarr")
    files = client.wait(
        lambda: (
            (rows := api("GET", "/episodefile?seriesId=" + str(series["id"])))
            and len(rows) == len(selected.episodes)
            and rows
        ),
        "Season did not import completely",
    )
    evidence.stamp("imported")
    root = Path("/media/dlna/TV") / case.library

    def routed():
        rows = [p for p in root.rglob("*.mkv") if p.name.startswith(case.prefix)]
        return rows if len(rows) == len(selected.episodes) else None

    imported = client.wait(routed, "TV files did not reach the intended library")
    if case.scenario == "season-pack" and os.environ.get("POMPEY_TEST_BASELINE") != "1":
        from release_recovery import reject_imported_pack
        selected = reject_imported_pack(client, case, series, files, selected)
        infohash = client.peer(selected)["infoHash"]
        active.update(infoHash=infohash,selectedRelease=selected.release,recoveryVerified=True)
        client.release(selected)
        client.wait(lambda:(t:=torrent()) and t["progress"]==1,"Replacement pack was not verified")
        command = api("POST","/command",{"name":"RefreshMonitoredDownloads"})
        client.wait_command(command["id"],"sonarr")
        files = client.wait(lambda:(rows:=api("GET","/episodefile?seriesId="+str(series["id"]))) and len(rows)==len(selected.episodes) and rows,
                            "Replacement season did not import completely")
        imported = client.wait(routed,"Replacement TV files did not reach the intended library")
    source = Path("/tmp/world") / selected.release
    episodes = api("GET", "/episode?seriesId=" + str(series["id"]))
    assert {
        (e["seasonNumber"], e["episodeNumber"]) for e in episodes if e["hasFile"]
    } == {(selected.season, n) for n in selected.episodes}, episodes
    for episode in [e for e in episodes if e["hasFile"]]:
        file = next(f for f in files if f["id"] == episode["episodeFileId"])
        assert file["mediaInfo"]["audioStreamCount"] == len(selected.audio_languages), (
            file["mediaInfo"]
        )
        target = next(p for p in imported if p.name == Path(file["relativePath"]).name)
        source_file = (
            source
            / f"{case.prefix}.S{episode['seasonNumber']:02d}E{episode['episodeNumber']:02d}.{selected.quality}.WEB-DL.mkv"
        )
        assert digest(target) == digest(source_file), "Wrong episode payload"
        subtitles = list(target.parent.glob(target.stem + "*.srt"))
        assert len(subtitles) == 1 and digest(subtitles[0]) == digest(
            source_file.with_suffix(".en.srt")
        ), "Missing or wrong episode subtitle"
        background.ledger.append(
            {
                "kind": "sonarr",
                "scenario": case.scenario,
                "prefix": case.prefix,
                "tmdbId": case.movie_id,
                "tvdbId": case.tvdb_id,
                "seasonNumber": episode["seasonNumber"],
                "episodeNumber": episode["episodeNumber"],
                "arrId": series["id"],
                "requestId": request["id"],
                "monitored": True,
                "seasons": actual_seasons,
                "since": active["wallStarted"],
                "infoHash": infohash,
                "cleanupDeadline": time.time() + 60,
                "path": str(target),
                "sha256": digest(target),
                "subtitle": str(subtitles[0]),
                "subtitleSha256": digest(subtitles[0]),
            }
        )
    transfer = client.peer(selected)
    assert transfer["sent"] >= transfer["payloadBytes"], transfer
    assert tunnel_bytes() - before >= transfer["payloadBytes"], (
        "TV payload bypassed tunnel"
    )
    if active.get("recoveryVerified"):
        from release_recovery import assert_pack_sequence
        assert_pack_sequence(client,series["id"],selected.release)
    save_json(ledger_file, background.ledger)
    active.update(
        payloadBytes=transfer["payloadBytes"],
        transferredBytes=transfer["sent"],
        libraryPath=str(root),
        priorTitlesVerified=len({r["prefix"] for r in background.ledger}) - 1,
        episodeFiles=len(files),
        seasonsVerified=actual_seasons,
        qualityProfile=profile["name"],
    )
