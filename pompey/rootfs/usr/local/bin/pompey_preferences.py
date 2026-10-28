"""Validated app preferences, independent of Supervisor environment options."""

import json
import os
from pathlib import Path
import time

import pompey_state as state

DEFAULTS = {"after_download": "stop_sharing", "simultaneous_downloads": 8,
            "prefer_dual_audio": True}


def validate(values):
    if not isinstance(values, dict) or values.keys() != DEFAULTS.keys():
        raise ValueError("Submit all supported preferences")
    if type(values["after_download"]) is not str or values["after_download"] not in {"stop_sharing", "share_to_ratio", "share_one_day"}:
        raise ValueError("Choose a supported seeding policy")
    if type(values["simultaneous_downloads"]) is not int or not 1 <= values["simultaneous_downloads"] <= 20:
        raise ValueError("Simultaneous downloads must be between 1 and 20")
    if type(values["prefer_dual_audio"]) is not bool:
        raise ValueError("Dual-audio preference must be enabled or disabled")
    # Retain the legacy key for stored/HA options; imports now always favor moves.
    return {**values, "after_download": "stop_sharing"}


def current():
    record = state.load("preferences")
    if record:
        return validate(record["values"])
    # Carry forward legacy HA options until the first save in Ingress. Explicit
    # environment overrides remain useful to the isolated acceptance harness.
    path = Path(os.environ.get("POMPEY_OPTIONS", "/data/options.json"))
    legacy = json.loads(path.read_text()) if path.exists() else {}
    values = dict(DEFAULTS)
    for key, variable in [("after_download", "AFTER_DOWNLOAD"),
                          ("simultaneous_downloads", "SIMULTANEOUS_DOWNLOADS")]:
        raw = os.environ.get(variable, legacy.get(key, values[key]))
        values[key] = int(raw) if key == "simultaneous_downloads" else raw
    return validate(values)


def save(values):
    from pompey_runtime import stack_lock
    values = validate(values)
    with stack_lock(blocking=False):
        state.save("preferences", {"values": values, "updated": time.time()})
    return status()


def status():
    values = current()
    applied = state.load("preferences-applied")
    return {"values": values, "pending": applied.get("values") != values,
            "updated": state.load("preferences").get("updated")}


def applied():
    state.save("preferences-applied", {"values": current(), "updated": time.time()})
