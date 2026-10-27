"""A clean two-boot protocol for the same container, not a scenario retry."""

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from support import save_json

PLAN = Path("/data/pompey-test/restart.json")


def source_signature(client):
    sources = [
        {"id": row["id"], "name": row["name"], "enable": row["enable"],
         "fields": {field["name"]: field.get("value") for field in row["fields"]
                    if field["name"] in {"baseUrl", "apiPath"}}}
        for row in client.prow("GET", "/indexer")
    ]
    return hashlib.sha256(json.dumps(sorted(sources, key=lambda row: row["id"]), sort_keys=True).encode()).hexdigest()


def pending_plan():
    if not PLAN.exists():
        return None
    plan = json.loads(PLAN.read_text())
    return plan if plan["phase"] == "prepared" else None


def prepare(client, invariants, remaining):
    evidence = client.evidence
    if os.environ.get("POMPEY_TEST_BASELINE") == "1":
        # Establish an upgrade fixture with the released implementation at its
        # production request cadence, not the accelerated candidate stress loop.
        # Persist its actual ownership ledger once the three requests are settled.
        from pompey_runtime import stack_lock
        with stack_lock():
            subprocess.run([sys.executable, "/usr/local/bin/wire_stack.py", "closeout"], check=True, timeout=60)
        ownership = json.loads(Path("/data/pompey/state/requests.json").read_text())
        expected = {c["tmdbId"] for c in evidence.data["cases"]}
        observed = {row["externalId"] for row in ownership["owned"].values()}
        assert expected <= observed and ownership.get("instanceId"), ownership
        evidence.data["releasedRequestOwnershipVerified"] = sorted(expected)
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
        "sourceSignature": source_signature(client),
    }
    save_json(PLAN, plan)
    evidence.data.update(restartRequired=True, phaseComplete=True)
    evidence.checkpoint()


def finish(client, plan):
    assert source_signature(client) == plan["sourceSignature"], "Configured indexer identity or address changed"
    # Fulfilment after restart/upgrade goes through the same real Prowlarr
    # source and authenticated fixture. API masking of secret fields is not a
    # durable credential representation to compare between application versions.
    save_json(PLAN, {**plan, "phase": "completed"})
    client.evidence.data["containerRestart"] = {
        "sameContainer": True,
        "completedTitlesPreserved": plan["completedTitles"],
        "pendingRequestsResumed": len(plan["pendingPrefixes"]),
    }
