"""User rejects three native releases, including an imported file, before success."""

import json
from pathlib import Path
from catalogue import offers
from support import HttpFailure

BASE = "http://127.0.0.1:8099/diagnostics"


def post(client, path, payload):
    def attempt():
        try:
            return client.session.call("POST", BASE + path, payload)
        except HttpFailure as exc:
            if exc.status != 409:
                raise
            return False  # Only the existing shared maintenance lock is retried.
    return client.wait(attempt, "Recovery could not acquire the maintenance lock", 20)


def reject_three_releases(client, case, movie):
    choices = offers(case)
    selected = choices[0]
    rejected = []
    for number in range(3):
        token = client.peer(selected)["infoHash"]
        if number == 2:
            client.release(selected)
            client.wait(lambda: (t := client.qbit("torrents/info?hashes=" + token)) and t[0]["progress"] == 1,
                        "Third release did not finish")
            client.refresh_downloads()
            def imported():
                row = client.arr("GET", "/movie/" + str(movie["id"]))
                return row if row["hasFile"] and row["path"].startswith("/media/dlna/Movies/") else None
            current = client.wait(imported, "Third release did not import and route")
            client.wait(lambda:not client.arr("GET","/movie/"+str(movie["id"]))["monitored"],
                        "Imported release did not stop monitoring before explicit retry")
            file = client.arr("GET", "/moviefile/" + str(current["movieFile"]["id"]))
            old_path = Path(file["path"])
            assert old_path.is_file(), file
            client.refresh_downloads()
            client.wait(lambda:not client.qbit("torrents/info?hashes="+token),"Imported torrent did not finish native cleanup",60)
            target = {"kind":"radarr","id":movie["id"],"fileId":file["id"]}
        else:
            client.refresh_downloads()
            client.wait(lambda:any(str(r.get("downloadId","")).lower()==token for r in client.arr("GET","/queue?page=1&pageSize=100")["records"]),
                        "Replacement did not reach the native Arr queue",10)
            old_path = None
            target = {"kind":"radarr","id":movie["id"],"downloadId":token}
        plan = post(client, "/recovery/preview", target)
        assert plan["downloadId"] == token and plan["release"] == selected.release, plan
        if old_path:
            assert str(old_path) in {f["path"] for f in plan["libraryFiles"]}, plan
        else:
            assert plan["downloadFiles"] and not plan["libraryFiles"], plan
        result = post(client, "/recovery/reject", {"kind":"radarr","id":movie["id"],"downloadId":token,
            "token":plan["token"],"confirmationId":plan["confirmationId"],"confirm":True,
            "reason":"wrong_content" if old_path else "other", "note":"Synthetic user rejection " + str(number+1)})
        assert result["ok"], result
        assert client.arr("GET","/movie/"+str(movie["id"]))["monitored"], "Explicit retry did not reopen monitoring"
        client.wait(lambda: not client.qbit("torrents/info?hashes=" + token), "Rejected torrent survived")
        if old_path:
            assert not old_path.exists(), "Rejected imported library file remains"
        blocklist = client.arr("GET", "/blocklist?page=1&pageSize=100")["records"]
        assert any(r["sourceTitle"] == selected.release for r in blocklist), blocklist
        rejected.append(token)
        def replacement():
            for choice in choices:
                status = client.peer(choice)
                if status["infoHash"] not in rejected and status["blocks"] > 0:
                    return choice
            return None
        selected = client.wait(replacement, "Arr did not choose an unblocked replacement", 45)
        assert selected.variant == number+1, selected
        assert all(not client.qbit("torrents/info?hashes=" + old) for old in rejected)
    return selected, client.peer(selected)["infoHash"]


def collected_sequence(client, kind, ident):
    def collected():
        payload = client.session.call("POST", BASE + "/report", {})
        report = json.loads(payload.split("\n\n",1)[1])
        sequence = next((r for r in report["sequences"] if r["kind"] == kind and r["id"] == ident),None)
        return sequence if sequence and sequence["status"] == "Imported" else None
    return client.wait(collected, "Release sequence did not retain the successful replacement", 15)


def assert_sequence(client, ident, final_release):
    sequence = collected_sequence(client,"radarr",ident)
    assert len(sequence["attempts"]) == 3, sequence
    assert all(a["stage"] == "retry-requested" and a["candidateSnapshot"].get("releases") for a in sequence["attempts"]), sequence
    chosen = {r["sourceTitle"] for r in sequence["nativeHistory"] if r["eventType"] == "grabbed"}
    assert len(chosen) == 4 and final_release in chosen, sequence
    assert sequence["attempts"][2]["removedFileIds"], "Imported rejection was not recorded"
    assert any(r["eventType"] == "downloadFolderImported" and r["sourceTitle"] == final_release for r in sequence["nativeHistory"]), sequence


def reject_imported_pack(client, case, series, imported_files, selected):
    """Replace a real imported season pack through exact native file identities."""
    paths = {Path(f["path"]) for f in imported_files}
    command=client.sonarr("POST","/command",{"name":"RefreshMonitoredDownloads"})
    client.wait_command(command["id"],"sonarr")
    original_hash=client.peer(selected)["infoHash"]
    client.wait(lambda:not client.qbit("torrents/info?hashes="+original_hash),"Imported pack did not finish native cleanup",60)
    plan = post(client, "/recovery/preview", {"kind":"sonarr","id":series["id"],"fileId":imported_files[0]["id"]})
    assert {Path(f["path"]) for f in plan["libraryFiles"]} == paths, plan
    assert len(plan["episodeIds"]) == 2, plan
    result = post(client, "/recovery/reject", {"kind":"sonarr","id":series["id"],"downloadId":plan["downloadId"],
        "token":plan["token"],"confirmationId":plan["confirmationId"],"confirm":True,
        "reason":"wrong_language","note":"Synthetic season-pack rejection"})
    assert result["ok"] and all(not p.exists() for p in paths), result
    assert any(r["sourceTitle"]==selected.release for r in client.sonarr("GET","/blocklist?page=1&pageSize=100")["records"])
    replacement = offers(case)[1]
    client.wait(lambda:client.peer(replacement)["blocks"] > 0,"Sonarr did not choose a replacement pack",45)
    assert not client.qbit("torrents/info?hashes="+plan["downloadId"])
    return replacement


def assert_pack_sequence(client, ident, final_release, final_hash):
    sequence = collected_sequence(client,"sonarr",ident)
    assert len(sequence["attempts"]) == 1, sequence
    assert len(sequence["attempts"][0]["removedFileIds"]) == 2, sequence
    assert sequence["attempts"][0]["search"]["request"]["name"] == "SeasonSearch", sequence
    assert any(r["eventType"] == "grabbed" and r["sourceTitle"] == final_release and str(r.get("downloadId", "")).lower() == final_hash for r in sequence["nativeHistory"]), sequence
    imported = [r for r in sequence["nativeHistory"] if r["eventType"] == "downloadFolderImported" and str(r.get("downloadId", "")).lower() == final_hash]
    assert len({r["episodeId"] for r in imported}) == 2, sequence
