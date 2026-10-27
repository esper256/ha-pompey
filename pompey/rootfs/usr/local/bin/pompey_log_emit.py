#!/usr/bin/env python3
"""Tag stdin (or argv text) with a service name and flush every line.

Drops Arr stack frames (NzbDrone is Servarr's internal name, not Usenet),
Seerr debug Plex-scan spam, ANSI color, and secrets so the HA log stays readable.
"""
from __future__ import annotations

import datetime
from collections import OrderedDict
import re
import sys
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Structured markers only. Bare "fail"/"unable"/"error" in JSON bodies and
# debug lines used to promote thousands of lines to WARNING/ERROR.
ERROR_RE = re.compile(
    r"\(C\)|\[(error|fatal|critical)\]|\|Error\||\|Fatal\||\|Critical\|",
    re.I,
)
WARN_RE = re.compile(
    r"\(W\)|\[(warn|warning)\]|\|Warn\||\|Warning\|",
    re.I,
)
EXCEPTION_RE = re.compile(
    r"Exception:|HTTP request failed:|TooManyRequests|CloudFlareProtection",
    re.I,
)

STACK_RE = re.compile(
    r"^\s+at \S+" r"|^\s+--- End of (?:inner exception|stack trace)",
    re.I,
)
SEERR_DEBUG_RE = re.compile(r"\[debug\]", re.I)
DUMP_BODY_RE = re.compile(
    r'^\s*[{}\[\],]*$'
    r'|^\s*"[^"]+"\s*:'
    r"|^\s*<error\b"
    r"|^\s*<\?xml\b",
    re.I,
)
ARR_CATEGORY_NOTE_RE = re.compile(
    r"^\s*-- : Query successful, but no results in the configured categories",
    re.I,
)
# Indexer sites 429 / CloudFlare / empty-category tests. Prowlarr's UI already
# shows source health. These are not disk or wiring failures.
INDEXER_FLAKE_RE = re.compile(
    r"HttpClient:\s*HTTP Error"
    r"|HTTP request failed:\s*\[(?:400|403|429|502|503|504)"
    r"|TooManyRequestsException"
    r"|CloudFlareProtection"
    r"|Cloudflare protection"
    r"|No Results in configured categories"
    r"|RadarrV3Proxy:\s*Invalid Request"
    r"|SonarrV3Proxy:\s*Invalid Request"
    r"|Unable to connect to indexer"
    r"|API Request Limit reached"
    r"|Error Parsing Rows Selector"
    r"|IndexerException"
    r"|Flaresolverr may be required"
    r"|RequestUri:"
    r"|FeedUrl:"
    r"|ReleaseSearchService:\s*Searching indexer"
    r"|Cardigann:"
    r"|ErrorPipeline:\s*Invalid request Validation failed"
    r"|^\s*-- : Unable to connect to indexer"
    r"|^\s*\]\s*$",
    re.I,
)
PROXY_ACCESS_RE = re.compile(r'^\s*\[prowlarr-arr\]\s+"[A-Z]+ /')
APIKEY_RE = re.compile(r"(apikey=)[^&\s\"']+", re.I)
JWT_RE = re.compile(
    r"(?:token:\s*)?eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    re.I,
)
NZBDRONE_RE = re.compile(r"NzbDrone\.", re.I)

_last_emitted: dict[str, str] = {}
_repeated = OrderedDict()
_native_time = re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?Z?")
_credential = re.compile(r"((?:X-Api-Key|Authorization|X-Plex-Token|password|api[_-]?key|token)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&;,]+)", re.I)
_authorization = re.compile(r"(Authorization[\"']?\s*[:=]\s*)(?:Bearer|Basic)\s+[^\s\"';,}]+", re.I)
_html_dump: set[str] = set()
HTML_START_RE = re.compile(r'<!doctype\s+html\b|<html\b|<head\b|<body\b', re.I)
LOG_RECORD_RE = re.compile(
    r'^(?:\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:\d\d)?\s*)?'
    r'(?:\[(?:trace|debug|info|warn|warning|error|fatal)\]|\|(?:Trace|Debug|Info|Warn|Error|Fatal)\|)|^\([NIWC]\)\s+\d{4}-', re.I)


def strip_ansi(line: str) -> str:
    return ANSI_RE.sub("", line)


def redact(line: str) -> str:
    line = _authorization.sub(r"\1(redacted)", line)
    line = _credential.sub(r"\1(redacted)", line)
    line = APIKEY_RE.sub(r"\1(redacted)", line)
    line = JWT_RE.sub("token:(redacted)", line)
    return NZBDRONE_RE.sub("Arr.", line)


def redact_value(value):
    """Apply the same best-effort filtering to native diagnostic messages."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def drop(name: str, line: str) -> bool:
    stripped = line.strip("\n")
    if not stripped.strip():
        return True
    if STACK_RE.search(stripped):
        return True
    if DUMP_BODY_RE.search(stripped):
        return True
    if ARR_CATEGORY_NOTE_RE.search(stripped):
        return True
    if INDEXER_FLAKE_RE.search(stripped) and level_for(stripped) == "INFO":
        return True
    if name.lower() in {"prowlarr-arr", "prowlarr"} and PROXY_ACCESS_RE.search(stripped):
        return True
    if name.lower() == "seerr" and SEERR_DEBUG_RE.search(stripped):
        return True
    return False


def level_for(line: str) -> str:
    if ERROR_RE.search(line):
        return "ERROR"
    if WARN_RE.search(line):
        return "WARNING"
    if EXCEPTION_RE.search(line):
        return "WARNING"
    return "INFO"


def emit(name: str, line: str) -> None:
    line = strip_ansi(line.rstrip("\n"))
    if name in _html_dump:
        if LOG_RECORD_RE.match(line):
            _html_dump.discard(name)  # A truncated page must not swallow later errors.
        else:
            if re.search(r'</html\s*>', line, re.I):
                _html_dump.discard(name)
            return
    html = HTML_START_RE.search(line)
    if html:
        if not re.search(r'</html\s*>', line, re.I):
            _html_dump.add(name)
        line = line[:html.start()].rstrip()  # Retain any error preceding the body.
    if drop(name, line):
        return
    line = redact(line)
    if len(line) > 8192:
        line = line[:8192] + " [line truncated]"
    if not line.strip():
        return
    tag = level_for(line)
    now = time.monotonic()
    flush_repeats(now)
    # Keep the first useful warning/error, then summarize repeats. Native
    # timestamps must not make an identical failure look like a new condition.
    if tag in {"ERROR", "WARNING"}:
        key = (name, _native_time.sub("", line))
        previous = _repeated.get(key)
        if previous and now - previous["time"] < 60:
            previous["count"] += 1
            _repeated.move_to_end(key)
            return
        if len(_repeated) >= 128:
            old_key, old = _repeated.popitem(last=False)
            repeat_summary(old_key, old)
        _repeated[key] = {"time": now, "count": 0, "level": tag}
    elif _last_emitted.get(name) == line:
        return
    _last_emitted[name] = line
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    dest = sys.stderr if tag in ("ERROR", "WARNING") else sys.stdout
    print(f"[{ts}] {tag}: [{name}] {line}", file=dest, flush=True)


def repeat_summary(key, row):
    if row["count"]:
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] {row['level']}: [{key[0]}] Previous condition repeated {row['count']} time(s): {key[1][:240]}", file=sys.stderr, flush=True)


def flush_repeats(now=None):
    for key, row in list(_repeated.items()):
        if now is None or now - row["time"] >= 60:
            repeat_summary(key, row)
            del _repeated[key]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: pompey_log_emit.py NAME [TEXT]", file=sys.stderr)
        return 2
    name = argv[1]
    if len(argv) > 2:
        emit(name, " ".join(argv[2:]))
        return 0
    for raw in sys.stdin:
        emit(name, raw)
    flush_repeats()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
