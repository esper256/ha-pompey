"""One integration checkpoint, with failure evidence that recovery cannot erase."""

from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import time
from support import BackgroundRegression, NativeCommandFailed, save_json


class InvariantFailure(AssertionError):
    category = "invariant-event"


def source_events():
    path = Path(os.environ["POMPEY_EVENT_LOG"])
    with path.open() as source:
        fcntl.flock(source, fcntl.LOCK_SH)
        return [json.loads(line) for line in source]


class CommandHistory:
    def __init__(self):
        self.failures = {}

    def observe(self, kind, commands, ledger):
        id_key = "movieId" if kind == "radarr" else "seriesId"
        plural = "movieIds" if kind == "radarr" else "seriesIds"
        for command in commands:
            if command["status"] not in {"failed", "aborted", "cancelled"}:
                continue
            body = command.get("body") or {}
            targets = {
                body.get(id_key),
                *(body.get(plural) or []),
                *[
                    m.get(id_key)
                    for m in (
                        body.get("movies" if kind == "radarr" else "series") or []
                    )
                ],
            }
            queued = command.get("queued")
            if not queued:
                continue
            timestamp = datetime.fromisoformat(
                queued.replace("Z", "+00:00")
            ).timestamp()
            for owner in ledger:
                if (
                    owner.get("kind", "radarr") != kind
                    or owner["arrId"] not in targets
                    or timestamp < owner["since"]
                ):
                    continue
                key = (kind, command["id"], owner["prefix"])
                self.failures.setdefault(
                    key,
                    {
                        "kind": "native-command-failed",
                        "service": kind,
                        "commandId": command["id"],
                        "name": command["name"],
                        "status": command["status"],
                        "owner": owner["prefix"],
                        "time": timestamp,
                    },
                )
        return list(self.failures.values())


class Invariants:
    def __init__(self, client, background, *, since=None):
        self.client = client
        self.background = background
        self.since = client.ready_at if since is None else since
        self.commands = CommandHistory()
        self.checkpoints = []
        # Retain startup events too, but only failures after initialization are
        # unexpected: before this point Seerr and the source are not configured.
        assert any(e["kind"] == "controller-started" for e in source_events()), (
            "Controller event producer did not start"
        )
        self.capture()

    def capture(self):
        events = source_events()
        all_events = [
            dict(e, phase="runtime" if e["time"] >= self.since else "setup")
            for e in events
        ]
        all_events.extend(self.commands.failures.values())
        save_json(self.client.evidence.root / "invariant-events.json", all_events)
        unexpected = [
            e
            for e in all_events
            if e.get("phase") != "setup"
            and e["kind"] in {"job-failed", "attention", "native-command-failed"}
        ]
        self.client.evidence.data["invariants"] = {
            "armedAt": self.since,
            "checkpoints": self.checkpoints,
            "unexpected": unexpected,
        }
        self.client.evidence.checkpoint()
        return unexpected

    def assert_events(self):
        unexpected = self.capture()
        if not unexpected:
            return
        first = unexpected[0]
        if first["kind"] == "native-command-failed":
            error = NativeCommandFailed(str(first))
            error.owner = first["owner"]
            raise error
        raise InvariantFailure(str(first))

    def pending_cleanup(self):
        client = self.client
        torrents = {t["hash"].lower(): t for t in client.qbit("torrents/info")}
        queues = {"radarr": client.arr("GET", "/queue?page=1&pageSize=100")["records"]}
        if any(r.get("kind") == "sonarr" for r in self.background.ledger):
            queues["sonarr"] = client.sonarr("GET", "/queue?page=1&pageSize=100")[
                "records"
            ]
        leftovers = [
            p
            for root in ["By Rating", "complete", "manual"]
            for p in (Path("/media/dlna/downloads") / root).rglob("*")
            if p.is_file()
        ]
        unwanted = {
            name: list(Path("/media/dlna").rglob(name))
            for name in {
                name
                for row in self.background.ledger
                for name in row.get("unwantedFiles", [])
            }
        }
        pending = []
        for row in self.background.ledger:
            kind = row.get("kind", "radarr")
            queue = queues[kind]
            id_key = "seriesId" if kind == "sonarr" else "movieId"
            for condition, field in [
                (row["infoHash"] not in torrents, "torrent retained"),
                (
                    not any(unwanted[name] for name in row.get("unwantedFiles", [])),
                    "unwanted release files retained",
                ),
                (
                    not any(
                        q.get(id_key) == row["arrId"]
                        or (q.get("downloadId") or "").lower() == row["infoHash"]
                        for q in queue
                    ),
                    "queue retained",
                ),
                (
                    not any(row["prefix"] in str(p) for p in leftovers),
                    "staging payload retained",
                ),
            ]:
                if not condition:
                    if row["infoHash"] in torrents:
                        torrent = torrents[row["infoHash"]]
                        if torrent["progress"] != 1:
                            raise BackgroundRegression(
                                row["prefix"], "completed download regressed", torrent
                            )
                    pending.append({"owner": row["prefix"], "field": field})
                    if time.time() > row["cleanupDeadline"]:
                        raise BackgroundRegression(
                            row["prefix"], field, "Cleanup deadline exceeded"
                        )
        return pending

    def checkpoint(self, label, *, full=False, fresh_since=None):
        started = time.monotonic()
        client = self.client
        self.assert_events()
        if fresh_since is not None:
            client.healthy(fresh_since)
        else:
            client.assert_health()
        pending = self.pending_cleanup()
        if full and pending:
            client.wait(
                lambda: not self.pending_cleanup(),
                "Owned downloads did not finish cleanup",
                60,
            )
            pending = []
            client.assert_health()
        self.background.verify(client, full=full)
        ledger = self.background.ledger
        # Check every owned title, including late failures from earlier cases.
        for kind, api in [("radarr", client.arr), ("sonarr", client.sonarr)]:
            self.commands.observe(kind, api("GET", "/command"), ledger)
        self.assert_events()
        pending_owners = {r["owner"] for r in pending}
        for case in client.evidence.data["cases"]:
            if "infoHash" not in case:
                continue
            case["stopSharingVerified"] = case["prefix"] not in pending_owners
            if case.get("unwantedFilesIncluded"):
                case["unwantedFilesPurged"] = not any(
                    item["owner"] == case["prefix"]
                    and item["field"] == "unwanted release files retained"
                    for item in pending
                )
            save_json(client.evidence.root / (case["prefix"] + ".json"), case)
        self.checkpoints.append(
            {
                "label": label,
                "fullChecksums": full,
                "freshHealth": fresh_since is not None,
                "titlesVerified": len({r["prefix"] for r in ledger}),
                "filesVerified": len(ledger),
                "pendingCleanup": pending,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        # Also catches source events emitted while the native snapshots ran.
        self.assert_events()
