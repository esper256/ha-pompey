"""Run real integration scenarios with shared clients, evidence and background checks."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.path[:0] = ["/tests/journey", "/usr/local/bin"]
from catalogue import cases, offers
from applications import ApplicationStack, Background, configure, identities
from movie_requests import movie_request
from tv_requests import tv_request
from stack_checks import rotation_scenario, assert_ui_access
from invariants import Invariants
import restart
from request_lifecycle import completed_closeout
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
        probe = ApplicationStack(client.secrets, session, client.evidence)
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
            ("sonarrSeries", lambda: probe.sonarr("GET", "/series")),
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
                    case = next(c for c in cases() if c.prefix == active["prefix"])
                    snapshots["peerEvidence"] = [
                        dict(probe.peer(offer), release=offer.release)
                        for offer in offers(case)
                    ]
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
        Path("/etc/resolv.conf"),
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
    try:
        dns = subprocess.run(
            [
                sys.executable,
                "-c",
                "import socket; print(socket.getaddrinfo('plex.tv', 443, type=socket.SOCK_STREAM))",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        snapshots["engineDnsProbe"] = {
            "returncode": dns.returncode,
            "stdout": dns.stdout,
            "stderr": dns.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        snapshots["engineDnsProbe"] = {"error": str(exc)}
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
    result = {
        "requests": [json.loads(row) for row in events.read_text().splitlines()]
        if events.exists()
        else [],
        "unknown": json.loads(unknown.read_text()) if unknown.exists() else [],
    }

    previous = Path("/artifacts/boot1-external-requests.json")
    if previous.exists():
        result["requests"] = [
            dict(r, boot=1) for r in json.loads(previous.read_text())
        ] + [dict(r, boot=2) for r in result["requests"]]
        result["unknown"] = (
            json.loads(Path("/artifacts/boot1-unexpected-requests.json").read_text())
            + result["unknown"]
        )
    return result


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
    plan = restart.pending_plan()
    prior = json.loads((root / "boot1-result.json").read_text()) if plan else None
    evidence = Evidence(
        root, started, json.loads(Path("/tmp/world/bootstrap-timings.json").read_text())
    )
    if plan:
        evidence.data = prior
        evidence.started = plan["firstStarted"]
        started = evidence.started
        evidence.data.pop("restartRequired", None)
        evidence.data["resumedContainer"] = True
        evidence.checkpoint()
    selected = json.loads(os.environ.get("POMPEY_TEST_SELECTED_CASES", "[0,1,2,3]"))
    catalogue = cases()
    evidence.data.setdefault(
        "requestedCases",
        [
            catalogue[i].prefix
            for i in selected
            if type(i) is int and 0 <= i < len(catalogue)
        ],
    )
    if (
        not selected
        or any(type(i) is not int or not 0 <= i < len(catalogue) for i in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("Invalid scenario selection")
    client = None
    invariants = None
    try:
        deadline = Deadline(STARTUP_TIMEOUT)
        session = Session(deadline)
        deadline.wait(
            lambda: Path("/data/pompey/secrets.json").is_file(),
            "Startup did not configure engines",
        )
        client = ApplicationStack(
            json.loads(Path("/data/pompey/secrets.json").read_text()), session, evidence
        )
        restored = os.environ.get("POMPEY_TEST_CONFIGURED") == "1" or bool(plan)
        configure(client, restored)
        ledger_file = Path("/data/pompey-test/evidence.json")
        background = Background(
            json.loads(ledger_file.read_text()) if ledger_file.exists() else []
        )
        assert bool(plan) or bool(background.ledger) == restored, (
            "Expected configured clone" if restored else "Expected empty fresh state"
        )
        evidence.data.update(
            configuredClone=os.environ.get("POMPEY_TEST_CONFIGURED") == "1",
            identities=identities(),
            controllerIntervals=json.loads(
                os.environ.get("POMPEY_JOB_INTERVALS", "{}")
            ),
        )
        assert_ui_access(client)
        invariants = Invariants(
            client, background, since=plan["armedAt"] if plan else None
        )
        if restored:
            invariants.checkpoint("configured-baseline", full=True)
        batch_since = time.time()
        evidence.data["willRestart"] = 9 in selected
        if plan:
            selected = [
                c.index for c in catalogue if c.prefix in plan["pendingPrefixes"]
            ] + plan["remaining"]
        for position, index in enumerate(selected):
            # Each scenario gets its own budget; it cannot consume a later one's time.
            session.deadline = Deadline(SCENARIO_TIMEOUT)
            action = (
                rotation_scenario
                if catalogue[index].scenario == "vpn-rotation"
                else tv_request
                if catalogue[index].kind == "tv"
                else movie_request
            )
            result = action(
                client,
                catalogue[index],
                background,
                ledger_file,
                **(
                    {
                        "resume": bool(plan)
                        and catalogue[index].prefix in plan["pendingPrefixes"]
                    }
                    if action == movie_request
                    else {}
                ),
            )
            if result in {"waiting", "restart"}:
                evidence.active["status"] = (
                    "waiting" if result == "waiting" else "paused"
                )
                evidence.checkpoint()
                evidence.active = None
                if result == "restart":
                    assert_contract()
                    restart.prepare(client, invariants, selected[position + 1 :])
                    return
                invariants.checkpoint(catalogue[index].prefix + "-waiting")
                continue
            invariants.checkpoint(
                catalogue[index].prefix,
                fresh_since=evidence.active.get("freshHealthSince"),
            )
            evidence.stamp("invariants-verified")
            evidence.finish()
            if plan and catalogue[index].scenario == "restart-partial":
                restart.finish(client, plan)
                completed_closeout(client, background, ledger_file)
                invariants.checkpoint("request-closeout", full=True)
        invariants.checkpoint("batch-complete", full=True, fresh_since=batch_since)
        external = assert_contract()
        dns = {
            r["name"]
            for r in external["requests"]
            if r.get("kind") == "dns" and r.get("source") == "10.77.0.1"
        }
        required = {"api.themoviedb.org"} | {
            "skyhook.sonarr.tv" if catalogue[i].kind == "tv" else "api.radarr.video"
            for i, case in enumerate(catalogue)
            if case.prefix in evidence.data["requestedCases"]
        }
        assert required <= dns, (
            "Real engine DNS missing",
            dns,
        )
        evidence.data["timings"]["batch-complete"] = round(
            time.monotonic() - started, 3
        )
        assert {c["prefix"] for c in evidence.data["cases"]} == set(
            evidence.data["requestedCases"]
        ), "A selected scenario did not execute"
        assert all(c["status"] == "passed" for c in evidence.data["cases"]), (
            "A selected scenario remains incomplete"
        )
        evidence.data["success"] = True
        evidence.checkpoint()
        print(
            "PASS: restarted container"
            if plan
            else "PASS: configured clone"
            if restored
            else "PASS: fresh install",
            "and",
            len(evidence.data["cases"]),
            "integration scenarios",
            flush=True,
        )
    except BaseException as exc:
        if invariants:
            try:
                invariants.capture()
            except Exception as diagnostic:
                print("Invariant evidence collection failed:", diagnostic, flush=True)
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
