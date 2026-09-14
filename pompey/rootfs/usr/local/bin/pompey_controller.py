#!/usr/bin/env python3
"""Independent bounded jobs with current health, retry backoff and one mutation lock."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time
import urllib.request
from engine_manager import HEALTH, atomic_json, stack_lock, run_process
from media_policy import attention


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

    def complete(self, now, success):
        self.failures = 0 if success else self.failures + 1
        self.due = now + (self.interval if success else min(300, 5 * 2 ** min(self.failures, 6)))
        self.future = None


def execute(job):
    if job.exclusive:
        with stack_lock(): run_process(job.args, job.timeout)
    else:
        run_process(job.args, job.timeout)


def health(ready):
    result = {}
    for name, url in HEALTH.items():
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                result[name] = response.status == 200
        except OSError:
            result[name] = False
    result['vpn'] = (ready/'vpn-up').exists()
    return result


def main():
    here = Path(__file__).resolve().parent
    ready = Path(os.environ.get('POMPEY_READY','/tmp/pompey'))
    jobs = [Job('configuration',[sys.executable,str(here/'wire_stack.py')],300,240),
            Job('requests',[sys.executable,str(here/'wire_stack.py'),'closeout'],15,120),
            Job('downloads',[sys.executable,str(here/'wire_stack.py'),'housekeep'],30,120),
            Job('routing',[sys.executable,str(here/'route_rating.py'),'--once'],60,120),
            Job('updates',['fetch-engines'],86400,1800,False,due=time.monotonic()+86400)]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        next_health = 0
        observed = {}
        while True:
            now = time.monotonic()
            for job in jobs:
                if job.future is not None and job.future.done():
                    try:
                        job.future.result()
                        job.complete(now, True)
                    except Exception as exc:
                        print(f'{job.name}: {exc}',flush=True)
                        job.complete(now, False)
                if job.future is None and now >= job.due and (ready/'engines-ready').exists():
                    job.future = pool.submit(execute,job)
            if now >= next_health:
                current = health(ready)
                if any(current.get(k) and not observed.get(k) for k in current):
                    jobs[0].due = 0
                observed = current
                try:
                    notices = attention()
                except (OSError, ValueError, TypeError) as exc:
                    notices = ['Policy state could not be read: ' + str(exc)]
                atomic_json(ready/'health.json',{'updated':time.time(),'services':current,'attention':notices,
                            'jobs':{j.name:{'failures':j.failures,'running':j.future is not None} for j in jobs}})
                next_health = now + 10
            time.sleep(1)


if __name__ == '__main__':
    main()
