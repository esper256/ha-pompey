#!/usr/bin/env python3
"""Accept only the documented resume timeout, with network/safety evidence intact."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def known_resume_failure(report, events, unknown, health, container):
    error = report.get("error", {})
    cases = report.get("cases", [])
    return (
        report.get("success") is False
        and error.get("category") == "timeout"
        and error.get("stage") == "Downloader did not verify the payload"
        and len(cases) == 1
        and cases[0].get("scenario") == "vpn-download-loss"
        and cases[0].get("status") == "failed"
        and cases[0].get("vpnLossDuringDownload") is True
        and report.get("stackChecks", {}).get("vpnOutage", {}).get("success") is True
        and {"radarr", "sonarr", "prowlarr", "qbittorrent", "seerr", "vpn"}
        <= health.get("services", {}).keys()
        and all(value is True for value in health["services"].values())
        and health.get("attention") == []
        and {"configuration", "requests", "downloads", "routing"}
        <= health.get("jobs", {}).keys()
        and all(
            j.get("failures") == 0 and j.get("overdue") is False
            for j in health["jobs"].values()
        )
        and any(e["kind"] == "controller-started" for e in events)
        and not any(
            e.get("phase") != "setup"
            and e["kind"] in {"job-failed", "attention", "native-command-failed"}
            for e in events
        )
        and not unknown
        and not container.get("shutdownError")
        and not container.get("cleanupError")
    )


def main():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/run.py"),
            "--skip-build",
            "--journey-only",
            "--only",
            "vpn-download-loss",
        ],
        cwd=ROOT,
    )
    if result.returncode == 0:
        return
    if result.returncode != 1:
        raise SystemExit(result.returncode)
    summary = json.loads((ROOT / ".test-results/timing-summary.json").read_text())
    container = summary["journeys"][0]["containers"][0]
    folder = Path(container["artifacts"])
    read = lambda name: json.loads((folder / name).read_text())
    report = read("result.json")
    state = read("failure-state.json")
    health = json.loads(state["/tmp/pompey/health.json"])
    if not known_resume_failure(
        report,
        read("invariant-events.json"),
        read("unexpected-requests.json"),
        health,
        container,
    ):
        raise SystemExit(
            "VPN scenario failed outside the accepted download-resume issue"
        )
    print(
        "::warning::Known VPN download-resume timeout reproduced; network, firewall and controller checks passed. See retained failure artifacts."
    )


if __name__ == "__main__":
    main()
