"""Change requests through Seerr and observe Pompey; never edit Arr monitoring."""

import json
from pathlib import Path
from support import digest, save_json


def close_request(client, row):
    tv = row.get("kind") == "sonarr"
    token = ("series:" if tv else "movie:") + str(row["arrId"])

    def observed():
        path = Path("/data/pompey/state/requests.json")
        return path.exists() and token in json.loads(path.read_text()).get("owned", {})

    client.wait(observed, f"Controller never observed ownership of {token}")
    client.search("DELETE", "/request/" + str(row["requestId"]))
    api = client.sonarr if tv else client.arr
    kind = "series" if tv else "movie"

    def stopped():
        item = api("GET", "/" + kind + "/" + str(row["arrId"]))
        return not item["monitored"] and (
            not tv
            or not any(
                s["monitored"]
                for s in item["seasons"]
                if s["seasonNumber"] in [int(k) for k in row["seasons"]]
            )
        )

    client.wait(
        stopped,
        f"Deleting request {row['requestId']} did not stop monitoring {token}",
    )
    return "deleted"


def completed_closeout(client, background, ledger_file):
    results = []
    for scenario in ["movie-pg", "single-episode"]:
        prefix = next(
            (r["prefix"] for r in background.ledger if r.get("scenario") == scenario),
            None,
        )
        if not prefix:
            assert not any(
                c.get("scenario") == scenario for c in client.evidence.data["cases"]
            ), "Missing closeout ledger owner"
            continue
        rows = [r for r in background.ledger if r["prefix"] == prefix]
        expected = close_request(client, rows[0])
        for row in rows:
            assert digest(row["path"]) == row["sha256"], (
                "Closing a request removed or changed media"
            )
            row.update(requestState=expected, monitored=False)
            if row.get("kind") == "sonarr":
                row["seasons"] = {k: False for k in row["seasons"]}
        results.append(
            {"owner": prefix, "action": "delete", "filesPreserved": len(rows)}
        )
    save_json(ledger_file, background.ledger)
    client.evidence.data["completedRequestCloseout"] = results
