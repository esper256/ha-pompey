"""Run real household scenarios with shared clients, evidence and background checks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.path[:0] = ["/tests/journey", "/usr/local/bin"]
from catalogue import cases
from household import Household, Background, configure, identities
from movie_requests import movie_request
from stack_checks import outage
from support import FixtureContractError
from support import (
    Deadline,
    Evidence,
    Session,
    save_json,
    tail,
    STARTUP_TIMEOUT,
    SCENARIO_TIMEOUT,
)


def collect_failure(client, root):
    """Use a separate bounded budget; diagnostics must never replace the failure."""
    snapshots = {}
    active = client.evidence.active if client else None
    if client:
        session = Session(Deadline(10), client.session.opener)
        probe = Household(client.secrets, session, client.evidence)
        probe.skey = client.skey
        for name, action in [
            ("radarrMovies", lambda: probe.arr("GET", "/movie")),
            ("radarrQueue", lambda: probe.arr("GET", "/queue?page=1&pageSize=100")),
            ("radarrCommands", lambda: probe.arr("GET", "/command")),
            ("radarrHistory", lambda: probe.arr("GET", "/history?page=1&pageSize=50")),
            (
                "radarrBlocklist",
                lambda: probe.arr("GET", "/blocklist?page=1&pageSize=50"),
            ),
            ("prowlarrStatus", lambda: probe.prow("GET", "/indexerstatus")),
            (
                "prowlarrHistory",
                lambda: probe.prow("GET", "/history?page=1&pageSize=50"),
            ),
            ("sonarrQueue", lambda: probe.sonarr("GET", "/queue?page=1&pageSize=100")),
            ("torrents", lambda: probe.qbit("torrents/info")),
            ("requests", lambda: probe.search("GET", "/request?take=100&skip=0")),
        ]:
            try:
                snapshots[name] = action()
            except Exception as exc:
                snapshots[name] = {"error": str(exc)}
        if active:
            try:
                if active.get("requestId"):
                    snapshots["requestDetail"] = probe.search(
                        "GET", "/request/" + str(active["requestId"])
                    )
                if active.get("infoHash"):
                    for endpoint in ["trackers", "properties"]:
                        try:
                            snapshots["torrent" + endpoint] = probe.qbit(
                                "torrents/" + endpoint + "?hash=" + active["infoHash"]
                            )
                        except Exception as exc:
                            snapshots["torrent" + endpoint] = {"error": str(exc)}
            except Exception as exc:
                snapshots["caseDetailError"] = str(exc)
    for file in [
        *[
            Path("/tmp/pompey") / name
            for name in ["configuration.json", "health.json", "status.json"]
        ],
        *Path("/config").glob("*/logs/*.txt"),
    ]:
        try:
            snapshots[str(file)] = tail(file)
        except OSError as exc:
            snapshots[str(file)] = {"error": str(exc)}
    save_json(root / "failure-state.json", snapshots)
    for cmd in [["wg", "show"], ["nft", "list", "table", "inet", "pompey"]]:
        try:
            subprocess.run(cmd, check=False, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


def transcript():
    # The fixture writes evidence outside its namespace too, so a broken tunnel
    # cannot erase the unknown endpoint responsible for an application failure.
    events = Path("/tmp/world/events/transcript.jsonl")
    unknown = Path("/tmp/world/events/unknown.json")
    return {
        "requests": [json.loads(row) for row in events.read_text().splitlines()]
        if events.exists()
        else [],
        "unknown": json.loads(unknown.read_text()) if unknown.exists() else [],
    }


def assert_contract():
    external = transcript()
    if external["unknown"]:
        raise FixtureContractError(str(external["unknown"]))
    return external


def main():
    if not __debug__:
        raise RuntimeError(
            "Acceptance requires Python assertions; disable PYTHONOPTIMIZE"
        )
    root = Path("/artifacts")
    started = float(Path("/tmp/world/started").read_text())
    evidence = Evidence(
        root, started, json.loads(Path("/tmp/world/bootstrap-timings.json").read_text())
    )
    selected = json.loads(os.environ.get("POMPEY_TEST_SELECTED_CASES", "[0,1,2,3]"))
    catalogue = cases()
    if (
        not selected
        or any(type(i) is not int or not 0 <= i < len(catalogue) for i in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Invalid scenario selection")
    client = None
    try:
        deadline = Deadline(STARTUP_TIMEOUT)
        session = Session(deadline)
        deadline.wait(
            lambda: Path("/data/pompey/secrets.json").is_file(),
            "Startup did not configure engines",
        )
        client = Household(
            json.loads(Path("/data/pompey/secrets.json").read_text()), session, evidence
        )
        restored = os.environ.get("POMPEY_TEST_CONFIGURED") == "1"
        configure(client, restored)
        ledger_file = Path("/data/pompey-test/evidence.json")
        background = Background(
            json.loads(ledger_file.read_text()) if ledger_file.exists() else []
        )
        assert bool(background.ledger) == restored, (
            "Expected configured clone" if restored else "Expected empty fresh state"
        )
        evidence.data.update(
            configuredClone=restored,
            identities=identities(),
            controllerIntervals=json.loads(
                os.environ.get("POMPEY_JOB_INTERVALS", "{}")
            ),
        )
        for index in selected:
            # Each scenario gets its own budget; it cannot consume a later one's time.
            session.deadline = Deadline(SCENARIO_TIMEOUT)
            movie_request(client, catalogue[index], background, ledger_file)
        background.verify(client, full=True)
        if 0 in selected:
            outage(client)
            background.verify(client, full=True)
        external = assert_contract()
        dns = {
            r["name"]
            for r in external["requests"]
            if r.get("kind") == "dns" and r.get("source") == "10.77.0.1"
        }
        assert {"api.radarr.video", "api.themoviedb.org"} <= dns, (
            "Real engine DNS missing",
            dns,
        )
        evidence.data["timings"]["batch-complete"] = round(
            time.monotonic() - started, 3
        )
        evidence.data["success"] = True
        evidence.checkpoint()
        print(
            "PASS: configured clone" if restored else "PASS: fresh install",
            "and",
            len(selected),
            "real household journeys",
            flush=True,
        )
    except BaseException as exc:
        unknown = transcript()["unknown"]
        if unknown:
            evidence.data["fixtureUnknown"] = unknown
            evidence.fail(FixtureContractError(str(unknown)))
            evidence.data["originalError"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            evidence.checkpoint()
        else:
            evidence.fail(exc)
        traceback.print_exc()
        try:
            collect_failure(client, root)
        except Exception as diagnostic:
            print("Failure evidence collection also failed:", diagnostic, flush=True)
        raise
    finally:
        external = transcript()
        save_json(root / "external-requests.json", external["requests"])
        save_json(root / "unexpected-requests.json", external["unknown"])


if __name__ == "__main__":
    main()
