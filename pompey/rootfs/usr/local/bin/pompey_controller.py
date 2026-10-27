#!/usr/bin/env python3
"""Independent bounded jobs with current health, retry backoff and one mutation lock."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
import json
from pathlib import Path
import sys
import time
import urllib.request
from pompey_runtime import HEALTH, atomic_json, stack_lock, run_process
from media_policy import attention
from pompey_events import record


@dataclass
class Job:
    name: str
    args: list
    interval: float
    timeout: float
    exclusive: bool = True
    due: float = 0
    failures: int = 0
    future: object = None
    phase: str = "idle"
    last_success: float | None = None
    started: float | None = None
    error: str = ""

    def complete(self, now, success):
        if not success:
            record("job-failed", job=self.name, error=self.error, started=self.started)
        self.phase = "idle"
        if success:
            self.last_success = time.time()
            self.error = ""

        self.failures = 0 if success else self.failures + 1
        self.due = now + (
            self.interval if success else min(300, 5 * 2 ** min(self.failures, 6))
        )
        self.future = None


def execute(job):
    if job.exclusive:
        try:
            with stack_lock(blocking=False):
                job.phase = "running"
                job.started = time.time()
                run_process(job.args, job.timeout)
        except BlockingIOError:
            job.phase = "waiting"
            return False
    else:
        job.phase = "running"
        job.started = time.time()
        run_process(job.args, job.timeout)
    return True


def dispatch(jobs, pool, now, ready):
    """Configure Arr first; routine jobs wait until the stack is wired."""
    candidates = [
        j
        for j in jobs
        if j.name == "configuration"
        or (
            ready
            / {
                "downloads": "downloads-wired",
                "requests": "seerr-arr",
                "routing": "roots-wired",
            }[j.name]
        ).exists()
    ]
    busy = any(j.exclusive and j.future is not None for j in candidates)
    priority = {"downloads": 0, "requests": 1, "routing": 2, "configuration": 3}
    for job in sorted(candidates, key=lambda j: (j.due, priority[j.name])):
        if job.future is not None or now < job.due:
            continue
        job.phase = "waiting"
        if job.exclusive and busy:
            continue
        job.future = pool.submit(execute, job)
        busy = busy or job.exclusive


def job_status(job, now):
    return {
        "failures": job.failures,
        "running": job.phase == "running",
        "phase": job.phase,
        "lastSuccess": job.last_success,
        "started": job.started,
        "error": job.error,
        "overdue": job.phase == "waiting" and now - job.due > max(60, job.interval),
    }


def health(ready):
    result = {}
    for name, url in HEALTH.items():
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                result[name] = response.status == 200
        except OSError:
            result[name] = False
    result["vpn"] = (ready / "vpn-up").exists()
    return result


def current_status(ready, services):
    from pompey_status import default_status

    data = default_status(search=services.get("seerr", False))
    try:
        configuration = json.loads((ready / "configuration.json").read_text())
    except (OSError, ValueError):
        configuration = {}
    # The unprivileged controller cannot inspect the root-private key directory.
    # Root networking publishes this non-secret marker after accepting a config.
    need_vpn = not (ready / "vpn-applied").exists()
    initialized = (ready / "seerr-arr").exists()
    complete = all(services.values()) and configuration.get("ok") and initialized
    data.update(
        updated=time.time(),
        need_vpn=need_vpn,
        search=services.get("seerr", False),
        step="ready" if complete else "wire",
        percent=100 if complete else 80,
        label="Ready" if complete else "Services require attention",
        error="",
    )
    if not services.get("vpn"):
        data["label"] = "VPN unavailable; local services remain accessible"
    if need_vpn:
        data["label"] = "Add a WireGuard configuration"
    if configuration.get("error"):
        data["error"] = configuration["error"]
    elif not initialized:
        data["error"] = "Complete search setup to connect requests to your libraries."
    from pompey_vpn_stats import sample

    data["vpn"] = sample()
    return data


def main():
    here = Path(__file__).resolve().parent
    ready = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
    jobs = [
        Job("configuration", [sys.executable, str(here / "wire_stack.py")], 60, 180),
        Job(
            "requests",
            [sys.executable, str(here / "wire_stack.py"), "closeout"],
            120,
            60,
        ),
        Job(
            "downloads",
            [sys.executable, str(here / "wire_stack.py"), "housekeep"],
            30,
            120,
        ),
        Job(
            "routing",
            [sys.executable, str(here / "route_rating.py"), "--once"],
            60,
            120,
        ),
    ]
    # The acceptance harness accelerates scheduling, not job bodies or deadlines.
    # Normal installations retain the intervals above.
    intervals = json.loads(os.environ.get("POMPEY_JOB_INTERVALS", "{}"))
    if not isinstance(intervals, dict) or intervals.keys() - {j.name for j in jobs}:
        raise ValueError("Unknown controller interval override")
    for job in jobs:
        if job.name in intervals:
            value = intervals[job.name]
            if type(value) not in {int, float} or not 1 <= value <= 3600:
                raise ValueError(
                    "Controller intervals must be between 1 and 3600 seconds"
                )
            job.interval = value
        if job.due == 0:
            job.due = time.monotonic()
    record("controller-started")
    with ThreadPoolExecutor(max_workers=2) as pool:
        next_health = 0
        observed = {}
        while True:
            now = time.monotonic()
            for job in jobs:
                if job.future is not None and job.future.done():
                    try:
                        if job.future.result():
                            job.complete(now, True)
                        else:
                            job.future = (
                                None  # Lock busy: waiting is not a failed operation.
                            )
                    except Exception as exc:
                        job.error = str(exc)
                        print(f"{job.name}: {exc}", flush=True)
                        job.complete(now, False)
            dispatch(jobs, pool, now, ready)
            if now >= next_health:
                current = health(ready)
                if any(current.get(k) and not observed.get(k) for k in current):
                    jobs[0].due = 0
                observed = current
                try:
                    notices = attention()
                except (OSError, ValueError, TypeError) as exc:
                    notices = ["Policy state could not be read: " + str(exc)]
                if notices:
                    record("attention", notices=notices)
                atomic_json(
                    ready / "health.json",
                    {
                        "updated": time.time(),
                        "services": current,
                        "attention": notices,
                        "jobs": {j.name: job_status(j, now) for j in jobs},
                    },
                )
                from pompey_status import lock_status
                import fcntl

                lock = lock_status()
                try:
                    atomic_json(ready / "status.json", current_status(ready, current))
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    os.close(lock)
                next_health = now + 5
            time.sleep(1)


if __name__ == "__main__":
    main()
