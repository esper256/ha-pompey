"""A clean two-boot protocol for the same container, not a scenario retry."""

import json
from pathlib import Path
from support import save_json

PLAN = Path("/data/pompey-test/restart.json")


def pending_plan():
    if not PLAN.exists():
        return None
    plan = json.loads(PLAN.read_text())
    return plan if plan["phase"] == "prepared" else None


def prepare(client, invariants, remaining):
    evidence = client.evidence
    invariants.checkpoint("before-container-restart", full=True)
    pending = [
        c for c in evidence.data["cases"] if c["status"] in {"waiting", "paused"}
    ]
    assert pending and any(c.get("resumeStage") == "partial" for c in pending), pending
    for case in pending:
        request = client.search("GET", "/request/" + str(case["requestId"]))
        assert request["media"]["tmdbId"] == case["tmdbId"], request
    plan = {
        "phase": "prepared",
        "firstStarted": evidence.started,
        "armedAt": invariants.since,
        "pendingPrefixes": [c["prefix"] for c in pending],
        "remaining": remaining,
        "completedTitles": len({r["prefix"] for r in invariants.background.ledger}),
    }
    save_json(PLAN, plan)
    evidence.data.update(restartRequired=True, phaseComplete=True)
    evidence.checkpoint()


def finish(client, plan):
    save_json(PLAN, {**plan, "phase": "completed"})
    client.evidence.data["containerRestart"] = {
        "sameContainer": True,
        "completedTitlesPreserved": plan["completedTitles"],
        "pendingRequestsResumed": len(plan["pendingPrefixes"]),
    }
