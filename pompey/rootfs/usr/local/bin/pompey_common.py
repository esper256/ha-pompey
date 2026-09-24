"""Shared paths, HTTP transport and durable configuration helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def secrets_path() -> str:
    return os.environ.get("POMPEY_SECRETS", "/data/pompey/secrets.json")


def ready_dir() -> str:
    return os.environ.get("POMPEY_READY", "/tmp/pompey")


def wait_tries() -> int:
    return int(os.environ.get("POMPEY_WAIT_TRIES", "90"))


def wait_sleep() -> float:
    return float(os.environ.get("POMPEY_WAIT_SLEEP", "2"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def after_download() -> str:
    raw = env("AFTER_DOWNLOAD", "stop_sharing").lower().replace("-", "_").replace(" ", "_")
    if raw in {"share_to_ratio", "ratio"}:
        return "share_to_ratio"
    if raw in {"share_one_day", "one_day"}:
        return "share_one_day"
    return "stop_sharing"


def simultaneous_downloads() -> int:
    raw = env("SIMULTANEOUS_DOWNLOADS", "8")
    try:
        number = int(raw)
    except ValueError:
        number = 8
    return max(1, min(number, 20))


def qbit_queue_preferences(active: int | None = None) -> dict:
    """WebUI names for the same queue policy write-engine-configs stamps in conf."""
    if active is None:
        active = simultaneous_downloads()
    return {
        "queueing_enabled": True,
        "max_active_downloads": active,
        "max_active_uploads": active,
        "max_active_torrents": max(active + 16, 20),
        "dont_count_slow_torrents": True,
        "slow_torrent_dl_rate_threshold": 2,
        "slow_torrent_ul_rate_threshold": 2,
        "slow_torrent_inactive_timer": 180,
    }


def qbit_share_limits(policy: str | None = None) -> dict:
    """Ratio and seeding minutes for the household policy.

    qBittorrent 5.2 stores these as GlobalMaxRatio / GlobalMaxSeedingMinutes.
    A negative value disables that limit. Ratio 0 is reached as soon as the
    download finishes. Arr removes the torrent later, after it imports.
    """
    chosen = policy or after_download()
    if chosen == "share_to_ratio":
        return {"ratio": 1, "seeding_minutes": -1}
    if chosen == "share_one_day":
        return {"ratio": -1, "seeding_minutes": 1440}
    return {"ratio": 0, "seeding_minutes": -1}


def qbit_seed_conf(policy: str | None = None) -> dict:
    """Keys qBittorrent 5.2 reads. Older MaxRatio* lines are ignored.

    ShareLimitAction stays Stop. The limit is reached when the download
    finishes, which is before Arr has imported. Remove drops the torrent Arr
    is watching, and RemoveWithContent deletes the file.
    """
    limits = qbit_share_limits(policy)
    return {
        r"Session\GlobalMaxRatio": str(limits["ratio"]),
        r"Session\GlobalMaxSeedingMinutes": str(limits["seeding_minutes"]),
        r"Session\GlobalMaxInactiveSeedingMinutes": "-1",
        r"Session\ShareLimitAction": "Stop",
    }


def qbit_seed_preferences(policy: str | None = None) -> dict:
    """WebAPI names. 5.2 accepts the value or the enabled flag, not both."""
    limits = qbit_share_limits(policy)
    prefs: dict = {
        "max_inactive_seeding_time_enabled": False,
        "max_ratio_act": 0,
    }
    if limits["ratio"] < 0:
        prefs["max_ratio_enabled"] = False
    else:
        prefs["max_ratio"] = limits["ratio"]
    if limits["seeding_minutes"] < 0:
        prefs["max_seeding_time_enabled"] = False
    else:
        prefs["max_seeding_time"] = limits["seeding_minutes"]
    return prefs


def qbit_set_preferences_url() -> str:
    return f"{qbit_url()}/api/v2/app/setPreferences"


def apply_qbit_queue() -> None:
    """Restamp queue and share limits on a running client.

    The conf file is only read at startup, and qBittorrent 5.2 ignores the
    MaxRatio keys older builds used. Push the live preferences too.
    """
    prefs = qbit_queue_preferences()
    prefs.update(qbit_seed_preferences())
    data = urllib.parse.urlencode({"json": json.dumps(prefs)}).encode()
    http("POST", qbit_set_preferences_url(), data)
    limits = qbit_share_limits()
    log(
        f"qbit queue max_active_downloads={prefs['max_active_downloads']} "
        f"max_active_torrents={prefs['max_active_torrents']} ignore_slow=true "
        f"share={after_download()} ratio={limits['ratio']} "
        f"seeding_minutes={limits['seeding_minutes']}"
    )


class ApiError(RuntimeError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def object_list(value, context, required=()):
    """Validate Arr snapshots before absence can authorize a mutation."""
    field_types = {'id': int, 'tmdbId': int, 'tvdbId': int, 'seasonNumber': int,
                   'path': str, 'rootFolderPath': str, 'name': str, 'status': str, 'monitored': bool}
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise RuntimeError('Invalid ' + context + ' snapshot')
    for row in value:
        for key in required:
            item = row.get(key)
            if item is None or item == '' or (key in field_types and type(item) is not field_types[key]):
                raise RuntimeError('Invalid ' + context + ' snapshot: ' + key)
    return value



def as_list(value):
    """HTTP JSON is usually an array of objects; stubs and error bodies often are not.

    Do not wrap an error object or a name-only dict as a one-item list.
    That made a failed GET look like "indexer already exists" and skipped setup.
    """
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if (value.get("message") or value.get("error")) and not any(
            key in value for key in ("id", "implementation", "path", "hostname")
        ):
            return []
        for key in ("results", "records", "data", "items"):
            inner = value.get(key)
            if isinstance(inner, list):
                return as_list(inner)
        if any(key in value for key in ("id", "hostname", "path", "implementation", "owned")):
            return [value]
        return []
    return []


def media_root() -> str:
    from pompey_config import media_root as root
    return str(root())


def library_dir(env_name: str, default: str) -> str:
    from pompey_config import library_dir as folder
    return str(folder(env_name, default))


def movies_dir() -> str:
    return library_dir("MEDIA_MOVIES", "Movies/Not Kid Friendly")


def movies_kid_dir() -> str:
    return library_dir("MEDIA_MOVIES_KID", "Movies/Kid Friendly")


def tv_dir() -> str:
    return library_dir("MEDIA_TV", "TV/Not Kid Friendly")


def tv_kid_dir() -> str:
    return library_dir("MEDIA_TV_KID", "TV/Kid Friendly")


AUTO_FOLDER = "By Rating"


def sibling_auto_dir(library_path: str) -> str:
    """0.3 staging root beside a general library. Not a Plex library."""
    parent = library_path.rstrip("/").rsplit("/", 1)[0]
    return f"{parent}/{AUTO_FOLDER}"


def movies_auto_dir() -> str:
    from pompey_config import staging_dirs
    return str(staging_dirs()[0])


def tv_auto_dir() -> str:
    from pompey_config import staging_dirs
    return str(staging_dirs()[1])


def legacy_movies_auto_dir() -> str:
    return sibling_auto_dir(movies_dir())


def legacy_tv_auto_dir() -> str:
    return sibling_auto_dir(tv_dir())


def downloads_complete() -> str:
    return f"{media_root()}/downloads/complete"


def downloads_manual() -> str:
    """Prowlarr Search → Grab. Not an Arr root folder and not complete/."""
    return f"{media_root()}/downloads/manual"


def downloads_recycle() -> str:
    """Arr recycle bin. Upgrades move the old library file here instead of deleting it."""
    return f"{media_root()}/downloads/recycle"


def resolved_path(path: str) -> str:
    full = os.path.normpath((path or "").strip())
    if not full:
        return ""
    if not os.path.isabs(full):
        full = os.path.normpath(os.path.join(media_root(), full))
    return os.path.realpath(full)


def paths_overlap(left: str, right: str) -> bool:
    a = resolved_path(left)
    b = resolved_path(right)
    if not a or not b:
        return False
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def plex_url() -> str:
    # Not an HA option. Household finishes Plex in Seerr. Tests may inject.
    return env("PLEX_URL")


def plex_token() -> str:
    return env("PLEX_TOKEN")


def indexer_url() -> str:
    # Not an HA option. Household adds sources in Prowlarr. Integration injects.
    return env("INDEXER_URL")


def indexer_api_key() -> str:
    return env("INDEXER_API_KEY")


def qbit_url() -> str:
    return env("QBIT_URL", "http://127.0.0.1:8080").rstrip("/")


def sonarr_url() -> str:
    return env("SONARR_URL", "http://127.0.0.1:8989").rstrip("/")


def radarr_url() -> str:
    return env("RADARR_URL", "http://127.0.0.1:7878").rstrip("/")


def arr_api_root(base: str, api_key: str) -> str:
    """Prefer /api/v3; use /api/v4 when v3 is gone. Do not hard-fail the wire."""
    host = base.rstrip("/")
    for ver in ("v3", "v4"):
        root = f"{host}/api/{ver}"
        try:
            http("GET", f"{root}/qualityprofile", headers=arr_headers(api_key))
            return root
        except RuntimeError as exc:
            msg = str(exc)
            if "-> 404" in msg:
                continue
            if "-> 400" in msg or "-> 401" in msg:
                return root
            continue
    log(f"Arr API v3/v4 missing at {host}; using /api/v3", "WARNING")
    return f"{host}/api/v3"


def prowlarr_url() -> str:
    return env("PROWLARR_URL", "http://127.0.0.1:9696").rstrip("/")


def prowlarr_arr_url() -> str:
    """Use native capabilities. No global ID-search rewriting."""
    return prowlarr_url()


def seerr_url() -> str:
    return env("SEERR_URL", "http://127.0.0.1:5055").rstrip("/")


def seerr_config_dir() -> str:
    return env("SEERR_CONFIG") or os.path.join(env("POMPEY_CONFIG", "/config"), "seerr")


def seerr_api_key_from_disk() -> str:
    """Seerr writes main.apiKey on first start. That key is admin; /auth/local is not.

    POST /api/v1/auth/local only signs in an existing local user. The first admin is
    created by the Plex (or Jellyfin) setup wizard. A generated pompey@local password
    therefore 403s on a real Seerr, which is why the wait screen came back after 0.2.13.
    """
    path = os.path.join(seerr_config_dir(), "settings.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    main = data.get("main") if isinstance(data.get("main"), dict) else data
    key = main.get("apiKey") if isinstance(main, dict) else None
    return key.strip() if isinstance(key, str) else ""


SEERR_HOUSEHOLD_PERMS = 32 + 128 + 256 + 512 + 8192


SEERR_REQUEST_ADVANCED = 8192


def seerr_permission_update(user: dict) -> dict | None:
    """PUT /api/v1/user/{id} body. Echoing GET fields 400s (email is read-only)."""
    try:
        perms = int(user.get("permissions") or 0)
    except (TypeError, ValueError):
        return None
    if perms & SEERR_REQUEST_ADVANCED:
        return None
    return {"permissions": perms | SEERR_REQUEST_ADVANCED}


def log(msg: str, level: str = "INFO") -> None:
    stamp = time.strftime("%H:%M:%S")
    caller = sys._getframe(1)
    source = os.path.basename(caller.f_globals.get("__file__", "pompey"))
    print(f"[{stamp}] {level}: [{source}] {msg}", flush=True)


def _quiet_path() -> str:
    return os.path.join(ready_dir(), "log-quiet.json")


def _load_quiet() -> dict:
    try:
        with open(_quiet_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_quiet(state: dict) -> None:
    path = _quiet_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, path)


def log_if_new(key: str, msg: str, level: str = "INFO") -> None:
    """Skip a housekeep line that has not changed since the last cycle.

    Housekeep is a new process every few minutes, so this is a small file
    under POMPEY_READY — not in-memory.
    """
    state = _load_quiet()
    if state.get(key) == msg:
        return
    state[key] = msg
    _save_quiet(state)
    log(msg, level)


def mark_status(step: str, label: str, percent: int, error: str = "") -> None:
    script = shutil.which("pompey_status.py")
    if not script:
        sibling = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pompey_status.py")
        if os.path.isfile(sibling):
            script = sibling
    if not script:
        return
    cmd = [sys.executable, script, step, label, str(percent)]
    if error:
        cmd.append(error)
    subprocess.run(cmd, check=False)


def load_secrets() -> dict:
    with open(secrets_path(), encoding="utf-8") as fh:
        return json.load(fh)


def http(method: str, url: str, body=None, headers=None, timeout=30):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = body
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(body, str):
            data = body.encode()
        else:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
    attempts = 3 if method == "GET" else 1
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                text = raw.decode(errors="replace")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        except urllib.error.HTTPError as exc:
            err = exc.read().decode(errors="replace")
            exc.close()
            last = ApiError(f"{method} {url} -> {exc.code} {err[:300]}", exc.code)
            if exc.code in {502, 503, 504} and attempt + 1 < attempts:
                time.sleep(wait_sleep())
                continue
            raise last from exc
        except urllib.error.URLError as exc:
            last = RuntimeError(f"{method} {url} -> {exc}")
            raise last from exc
    raise last or RuntimeError(f"{method} {url} failed")


def wait_http(url: str, tries: int | None = None):
    last = None
    n = wait_tries() if tries is None else tries
    for _ in range(n):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code < 500:
                return b""
            last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(wait_sleep())
    raise RuntimeError(f"timeout {url}: {last}")


def arr_headers(api_key: str) -> dict:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


