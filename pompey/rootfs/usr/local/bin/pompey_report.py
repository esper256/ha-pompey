"""Export durable release sequences; general application output stays in HA Logs."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from pompey_recovery import read_sequence, sequence_paths, reconcile
from pompey_runtime import stack_lock
import pompey_state as state
from pompey_recovery import MAX_SEQUENCES, MAX_EVENTS


def collect_report():
    sequences = []
    notices = []
    try:
        with stack_lock(blocking=False):
            reconcile()
    except (BlockingIOError, OSError, ValueError) as exc:
        notices.append("Using saved sequence observations: " + str(exc))
    remaining = 8 * 1024 * 1024
    for path in sequence_paths():
        try:
            size = path.stat().st_size
            if size > remaining:
                notices.append("Older sequences omitted by the 8 MiB export limit")
                break
            sequences.append(read_sequence(path)); remaining -= size
        except (OSError, ValueError) as exc:
            notices.append(path.name + ": " + str(exc))
    manifest = Path("/usr/share/pompey/engines.json")
    report = {"schema": "pompey-release-sequences-v1", "created": datetime.now(timezone.utc).isoformat(),
              "pompeyVersion": os.environ.get("BUILD_VERSION", "unknown"), "build": os.environ.get("BUILD_REF") or None,
              "sequences": sequences, "collectionNotices": notices,
              "retention": {"maxSequences": MAX_SEQUENCES, "maxAttemptsPerSequence": 20, "maxNativeEventsPerSequence": MAX_EVENTS,
                            "observationDays": 30, "exportByteLimit": 8 * 1024 * 1024, **state.load("release-retention")},
              "interpretation": ["Only explicit Try another incidents are retained.",
                  "Imported records native technical success, not human approval.",
                  "Peer counts and candidate snapshots are timestamped observations, not a reconstruction of the original selection.",
                  "General application errors and logs are available in Home Assistant Logs."]}
    if manifest.exists():
        report["bundle"] = json.loads(manifest.read_text())
    return "Pompey release sequence diagnostics\n\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n"
