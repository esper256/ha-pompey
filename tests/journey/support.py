"""Small reusable transport, deadline and durable evidence helpers for real journeys."""

from http.cookiejar import CookieJar
import hashlib
import json
from pathlib import Path
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

STARTUP_TIMEOUT = 180
SCENARIO_TIMEOUT = 120


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def tail(path, limit=12000):
    with Path(path).open("rb") as source:
        source.seek(0, 2)
        source.seek(max(0, source.tell() - limit))
        return source.read().decode(errors="replace")


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class FixtureContractError(AssertionError):
    category = "fixture-contract"


class NativeCommandFailed(AssertionError):
    category = "native-command-failed"


class BackgroundRegression(AssertionError):
    category = "background-regression"

    def __init__(self, owner, field, detail):
        self.owner = owner
        self.field = field
        super().__init__(f"{owner}: {field}: {detail}")


class WaitTimeout(AssertionError):
    category = "timeout"

    def __init__(self, stage, attempts, last):
        self.stage = stage
        super().__init__(f"{stage} after {attempts} observations: {last}")


class HttpFailure(RuntimeError):
    def __init__(self, method, url, status, detail):
        self.status = status
        # Query strings can contain credentials; keep them out of failure messages.
        safe = url.split("?", 1)[0]
        super().__init__(f"{method} {safe}: HTTP {status}: {detail[:1000]}")


class Deadline:
    def __init__(self, seconds):
        self.until = time.monotonic() + seconds

    def remaining(self, maximum=30):
        remaining = self.until - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Journey deadline exceeded")
        return min(maximum, remaining)

    def wait(self, predicate, message, seconds=60):
        until = min(self.until, time.monotonic() + seconds)
        original = self.until
        self.until = until
        attempts = 0
        last = ""
        try:
            while time.monotonic() < until:
                attempts += 1
                try:
                    result = predicate()
                    if result:
                        return result
                    last = "condition not met"
                except HttpFailure as exc:
                    if exc.status not in {502, 503, 504}:
                        raise  # Authentication/contract errors must fail immediately.
                    last = str(exc)
                except (OSError, urllib.error.URLError) as exc:
                    last = str(exc)
                time.sleep(min(0.2, max(0, until - time.monotonic())))
            raise WaitTimeout(message, attempts, last)
        finally:
            self.until = original


class Session:
    def __init__(self, deadline, opener=None):
        self.deadline = deadline
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def call(self, method, url, body=None, key=None, form=False):
        headers = {}
        data = None
        if body is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            data = (
                urllib.parse.urlencode(body).encode()
                if form
                else json.dumps(body).encode()
            )
        if key:
            headers["X-Api-Key"] = key
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(
                request, timeout=self.deadline.remaining()
            ) as response:
                raw = response.read()
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except ValueError:
                    return raw.decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            exc.close()
            if key:
                detail = detail.replace(key, "[redacted]")
            raise HttpFailure(method, url, exc.code, detail) from None


class Evidence:
    def __init__(self, root, started, initial=None):
        self.root = Path(root)
        self.started = started
        self.active = None
        self.data = {"success": False, "timings": initial or {}, "cases": []}
        self.checkpoint()

    def checkpoint(self):
        save_json(self.root / "result.json", self.data)
        if self.active:
            save_json(self.root / (self.active["prefix"] + ".json"), self.active)

    def stamp(self, name):
        target = self.active if self.active else self.data
        origin = target["started"] if self.active else self.started
        key = (
            "restart-" + name
            if not self.active and self.data.get("resumedContainer")
            else name
        )
        target["timings"][key] = round(time.monotonic() - origin, 3)
        print(
            "TIMING",
            self.active["prefix"] if self.active else "startup",
            name,
            target["timings"][key],
            flush=True,
        )
        self.checkpoint()

    def begin(self, case):
        self.active = {
            "prefix": case.prefix,
            "scenario": getattr(case, "scenario", "movie"),
            "tmdbId": case.movie_id,
            "certification": case.certification,
            "library": case.library,
            "started": time.monotonic(),
            "wallStarted": time.time(),
            "budgetSeconds": SCENARIO_TIMEOUT,
            "timings": {},
            "status": "running",
        }
        self.data["cases"].append(self.active)
        self.checkpoint()

    def finish(self):
        self.active["status"] = "passed"
        self.active["durationSeconds"] = round(
            time.monotonic() - self.active["started"], 3
        )
        self.stamp("outcome-verified")
        self.active = None

    def fail(self, exc):
        self.data["error"] = {
            "category": getattr(
                exc,
                "category",
                "timeout"
                if isinstance(exc, TimeoutError)
                else "application-http"
                if isinstance(exc, HttpFailure)
                else "assertion"
                if isinstance(exc, AssertionError)
                else "harness",
            ),
            "owner": getattr(exc, "owner", None),
            "field": getattr(exc, "field", None),
            "stage": getattr(exc, "stage", None),
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "elapsedSeconds": round(time.monotonic() - self.started, 3),
            "case": self.active["prefix"] if self.active else None,
            "lastCheckpoint": next(reversed(self.active["timings"]), None)
            if self.active
            else next(reversed(self.data["timings"]), None),
        }
        print(
            "FAIL",
            self.data["error"]["category"],
            self.data["error"]["owner"] or self.data["error"]["case"] or "startup",
            str(exc),
            flush=True,
        )
        if self.active:
            self.active["status"] = "failed"
        self.checkpoint()


class FileChecks:
    """Cache verified bytes only while inode, size, mtime and ctime stay unchanged."""

    def __init__(self):
        self.checked = {}

    def verify(self, path, expected, full=False):
        stat = Path(path).stat()
        fingerprint = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            expected,
        )
        if full or self.checked.get(path) != fingerprint:
            assert digest(path) == expected, ("Earlier file changed", str(path))
            self.checked[path] = fingerprint
