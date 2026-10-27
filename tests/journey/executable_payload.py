"""A plausible release with a double-extension executable transfers no payload."""

from pathlib import Path
import time


def executable_payload(client, case, background, ledger_file):
    client.evidence.begin(case)
    request = client.search(
        "POST", "/request", {"mediaType": "movie", "mediaId": case.movie_id}
    )
    client.evidence.active["requestId"] = request["id"]
    infohash = client.peer(case)["infoHash"]

    def files():
        rows = client.qbit("torrents/info?hashes=" + infohash)
        if not rows:
            return None
        entries = client.qbit("torrents/files?hash=" + infohash)
        return entries and (rows[0], entries)

    torrent, entries = client.wait(files, "Arr did not submit the executable fixture")
    assert entries and all(e["name"].endswith(".mkv.EXE") for e in entries), entries
    assert all(e["priority"] == 0 and e["progress"] == 0 for e in entries), entries
    # Make every fixture block available and observe a real maintenance cycle;
    # a closed fixture gate cannot make this assertion pass accidentally.
    client.release(case)
    since = time.time()
    client.refresh_downloads()
    client.wait(
        lambda: client.session.call("GET", "http://127.0.0.1:8099/health.json")
        ["jobs"]["downloads"]["lastSuccess"] > since,
        "Download maintenance did not inspect the excluded payload",
    )
    torrent, entries = files()
    peer = client.peer(case)
    assert peer["blocks"] == 0 and peer["sent"] == 0, peer
    assert torrent["downloaded"] == 0 and torrent["completed"] == 0, torrent
    assert all(e["priority"] == 0 and e["progress"] == 0 for e in entries), entries
    assert not [
        p for p in Path("/media/dlna").rglob("*")
        if p.is_file() and case.prefix in str(p) and p.suffix.lower() == ".exe"
    ], "Executable payload was materialized in media storage"
    movie = next(m for m in client.arr("GET", "/movie") if m["tmdbId"] == case.movie_id)
    assert not movie["hasFile"], movie
    client.evidence.active.update(
        executableFilesSkipped=entries,
        payloadBytesTransferred=peer["sent"],
        payloadRequests=peer["blocks"],
        nativeTorrentState=torrent["state"],
        imported=False,
        priorTitlesVerified=len({row["prefix"] for row in background.ledger}),
    )
