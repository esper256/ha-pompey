#!/usr/bin/env python3
"""Reconcile localhost engine configuration. Imports and request policy run separately."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pompey_common import (
    ready_dir,
    wait_tries,
    wait_sleep,
    env,
    after_download,
    apply_qbit_queue,
    as_list,
    movies_dir,
    movies_kid_dir,
    tv_dir,
    tv_kid_dir,
    legacy_movies_auto_dir,
    legacy_tv_auto_dir,
    movies_auto_dir,
    tv_auto_dir,
    downloads_complete,
    downloads_manual,
    downloads_recycle,
    paths_overlap,
    media_root,
    plex_url,
    plex_token,
    indexer_url,
    indexer_api_key,
    qbit_url,
    sonarr_url,
    radarr_url,
    arr_api_root,
    object_list,
    prowlarr_url,
    prowlarr_arr_url,
    seerr_url,
    seerr_api_key_from_disk,
    seerr_permission_update,
    log,
    log_if_new,
    mark_status,
    load_secrets,
    http,
    wait_http,
    arr_headers,
    SEERR_HOUSEHOLD_PERMS,
    SEERR_REQUEST_ADVANCED,
)
import pompey_state as persist


def ensure_root_folder(base: str, api_key: str, path: str) -> None:
    if paths_overlap(path, downloads_complete()) or paths_overlap(path, downloads_manual()):
        raise ValueError(f"Arr root overlaps a download folder: {path}")
    existing = as_list(http("GET", f"{base}/rootfolder", headers=arr_headers(api_key)))
    if any(item.get("path", "").rstrip("/") == path.rstrip("/") for item in existing):
        return
    try:
        http("POST", f"{base}/rootfolder", {"path": path}, headers=arr_headers(api_key))
    except RuntimeError as exc:
        raise RuntimeError(f"Could not create root folder {path}") from exc
    log(f"root folder {path}")


def wanted_arr_roots(kind: str) -> tuple[str, str, str]:
    """auto, general, kid. Same three names for movies and TV."""
    if kind == "sonarr":
        return tv_auto_dir(), tv_dir(), tv_kid_dir()
    return movies_auto_dir(), movies_dir(), movies_kid_dir()


LEGACY_ROOT_NAMES = {"tv", "movies", "kid friendly tv", "kid friendly movies"}


def in_root(path: str, root: str) -> bool:
    path = (path or "").rstrip("/")
    root = (root or "").rstrip("/")
    return bool(root) and (path == root or path.startswith(root + "/"))


def is_retired_staging_root(path: str, kind: str) -> bool:
    """Earlier releases staged By Rating beside the general library."""
    path = (path or "").rstrip("/")
    legacy = legacy_movies_auto_dir() if kind == "radarr" else legacy_tv_auto_dir()
    return bool(path) and path == legacy.rstrip("/")


def staging_holds_files(path: str) -> bool:
    """True when the folder contains a file. Empty directories do not count."""
    try:
        for _dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
            if filenames:
                return True
    except OSError:
        return True
    return False


def remove_empty_staging_tree(path: str) -> None:
    """Remove a retired staging folder once only empty directories remain."""
    folder = (path or "").rstrip("/")
    if not folder or not os.path.isdir(folder) or staging_holds_files(folder):
        return
    try:
        for dirpath, _dirnames, filenames in os.walk(folder, topdown=False, followlinks=False):
            if filenames or os.path.islink(dirpath):
                return
            os.rmdir(dirpath)
    except OSError as exc:
        log(f"empty staging folder {folder} left in place: {exc}", "WARNING")
        return
    log(f"removed empty staging folder {folder}")


def is_known_legacy_root(path: str, wanted: set[str]) -> bool:
    """True for unused 0.2.20 / 0.2.21 Pompey names, not an arbitrary extra library."""
    path = (path or "").rstrip("/")
    if not path or path in wanted:
        return False
    name = path.rsplit("/", 1)[-1].lower()
    if name not in LEGACY_ROOT_NAMES:
        return False
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    allowed = {media_root().rstrip("/"), "/media"}
    for household in wanted:
        folder = household.rstrip("/")
        if "/" in folder:
            allowed.add(folder.rsplit("/", 1)[0])
    return parent in allowed


def root_is_occupied(path: str, titles: list, lists: list, registered: list[str]) -> bool:
    leftover = (path or "").rstrip("/")
    if not leftover:
        return False
    others = [item.rstrip("/") for item in registered if item.rstrip("/") and item.rstrip("/") != leftover]
    for item in titles:
        assigned = str(item.get("rootFolderPath") or "").rstrip("/")
        if assigned == leftover:
            return True
        if assigned:
            continue
        loc = title_library_path(item)
        if loc and in_root(loc, leftover) and not any(in_root(loc, other) for other in others):
            return True
    for item in lists:
        if str(item.get("rootFolderPath") or "").rstrip("/") == leftover:
            return True
    return False


def prune_root_folders(base: str, api_key: str, kind: str) -> list[str]:
    """Unregister empty historical Pompey roots. Never move media or drop a used library."""
    auto, gen, kid = wanted_arr_roots(kind)
    wanted = {auto.rstrip("/"), gen.rstrip("/"), kid.rstrip("/")}
    notices: list[str] = []
    try:
        existing = object_list(http("GET", f"{base}/rootfolder", headers=arr_headers(api_key)), "root folders", ("id", "path"))
    except RuntimeError as exc:
        log(f"{kind} root folders: {exc}", "WARNING")
        return [f"{kind} library folders could not be checked; extra roots were left registered"]
    registered = [str(item.get("path") or "").rstrip("/") for item in existing]
    try:
        titles = object_list(
            http("GET", f"{base}/{arr_title_collection(kind)}", headers=arr_headers(api_key)), "library titles", ("id", "path")
        )
        lists = object_list(http("GET", f"{base}/importlist", headers=arr_headers(api_key)), "import lists", ("id",))
    except RuntimeError as exc:
        log(f"{kind} leftover roots: {exc}", "WARNING")
        return [f"{kind} library contents could not be checked; extra roots were left registered"]
    for item in existing:
        path = (item.get("path") or "").rstrip("/")
        ident = item.get("id")
        if not path or path in wanted:
            continue
        label = "movie" if kind == "radarr" else "TV"
        retired_staging = is_retired_staging_root(path, kind)
        if not retired_staging and not is_known_legacy_root(path, wanted):
            msg = f"Extra {label} library folder left registered: {path}"
            log(msg, "WARNING")
            notices.append(msg)
            continue
        if retired_staging and staging_holds_files(path):
            msg = f"Historical {label} staging folder still has files, left registered: {path}"
            log(msg, "WARNING")
            notices.append(msg)
            continue
        if root_is_occupied(path, titles, lists, registered):
            msg = f"Historical {label} library folder still in use, left registered: {path}"
            log(msg, "WARNING")
            notices.append(msg)
            continue
        if ident is None:
            msg = f"Historical {label} library folder has no id, left registered: {path}"
            log(msg, "WARNING")
            notices.append(msg)
            continue
        try:
            http(
                "DELETE",
                f"{base}/rootfolder/{ident}",
                headers=arr_headers(api_key),
            )
            log(f"removed unused leftover root folder {path}")
            if retired_staging:
                remove_empty_staging_tree(path)
        except RuntimeError as extra:
            msg = f"Could not unregister unused {label} library folder {path}"
            log(f"{msg}: {extra}", "WARNING")
            notices.append(msg)
    legacy = (legacy_movies_auto_dir() if kind == "radarr" else legacy_tv_auto_dir()).rstrip("/")
    if legacy and legacy not in registered and not staging_holds_files(legacy):
        remove_empty_staging_tree(legacy)
    return notices


def sync_root_folders(base: str, api_key: str, kind: str) -> list[str]:
    auto, gen, kid = wanted_arr_roots(kind)
    for path in (auto, gen, kid):
        ensure_root_folder(base, api_key, path)
    return prune_root_folders(base, api_key, kind)


def schema_impl(schema, name: str):
    for item in as_list(schema):
        if item.get("implementation") == name:
            return json.loads(json.dumps(item))
    raise RuntimeError(f"schema missing {name}")


def fill_fields(resource: dict, values: dict) -> dict:
    for field in resource.get("fields") or []:
        n = field.get("name")
        if n in values:
            field["value"] = values[n]
    return resource


def set_app_fields(resource: dict, values: dict) -> dict:
    """Set Prowlarr application fields, appending any the existing row omitted."""
    fill_fields(resource, values)
    fields = resource.get("fields")
    if not isinstance(fields, list):
        fields = []
        resource["fields"] = fields
    have = {field.get("name") for field in fields}
    for name, value in values.items():
        if name not in have:
            fields.append({"name": name, "value": value})
    return resource


RADARR_SYNC_CATS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]


SONARR_SYNC_CATS = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5060, 5070, 5080, 5090]


SONARR_ANIME_STANDARD_SEARCH = "syncAnimeStandardFormatSearch"


def app_field(app: dict, name: str):
    for field in app.get("fields") or []:
        if field.get("name") == name:
            return field.get("value")
    return None


def field_is_true(resource: dict, name: str) -> bool:
    return app_field(resource, name) is True


def cats_match(value, want: list[int]) -> bool:
    if not isinstance(value, list):
        return False
    try:
        have = sorted(int(item) for item in value)
    except (TypeError, ValueError):
        return False
    return have == sorted(want)


def sync_cats_for(impl: str) -> list[int]:
    return list(RADARR_SYNC_CATS if impl == "Radarr" else SONARR_SYNC_CATS)


def qbit_client_values(secrets: dict, kind: str) -> tuple[str, dict]:
    if kind == "radarr":
        cat = "radarr"
    elif kind == "sonarr":
        cat = "sonarr"
    elif kind == "prowlarr":
        cat = "prowlarr"
    else:
        raise ValueError(f"unknown download client kind {kind!r}")
    values = {
        "host": "127.0.0.1",
        "port": 8080,
        "username": secrets["qbit_user"],
        "password": secrets["qbit_password"],
        "movieCategory": cat,
        "tvCategory": cat,
        "category": cat,
        "useSsl": False,
    }
    if kind == "prowlarr":
        # Prowlarr Search → Grab picks a category from the release type.
        values["musicCategory"] = cat
        values["bookCategory"] = cat
    return cat, values


def download_client_ready(item: dict, cat: str, remove: bool) -> bool:
    if item.get("removeCompletedDownloads") is not remove:
        return False
    if item.get("removeFailedDownloads") is not True:
        return False
    if item.get("enable") is not True:
        return False
    return cat in {
        app_field(item, "movieCategory"),
        app_field(item, "tvCategory"),
        app_field(item, "category"),
    }


def apply_download_client(item: dict, values: dict, remove: bool) -> dict:
    updated = json.loads(json.dumps(item))
    set_app_fields(updated, values)
    updated["enable"] = True
    updated["removeCompletedDownloads"] = remove
    updated["removeFailedDownloads"] = True
    return updated


def ensure_download_client(base: str, api_key: str, secrets: dict, kind: str) -> None:
    remove = True
    cat, values = qbit_client_values(secrets, kind)
    existing = as_list(http("GET", f"{base}/downloadclient", headers=arr_headers(api_key)))
    for item in existing:
        if item.get("implementation") != "QBittorrent" or item.get("id") is None:
            continue
        if download_client_ready(item, cat, remove) and all(app_field(item, k) == v for k, v in values.items() if app_field(item, k) is not None):
            return
        updated = apply_download_client(item, values, remove)
        http(
            "PUT",
            f"{base}/downloadclient/{item['id']}",
            updated,
            headers=arr_headers(api_key),
        )
        log(f"{kind} download client category={cat} after_download={after_download()} remove={remove}")
        return
    schema = http("GET", f"{base}/downloadclient/schema", headers=arr_headers(api_key))
    client = apply_download_client(schema_impl(schema, "QBittorrent"), values, remove)
    client["name"] = "qBittorrent"
    client["priority"] = 1
    http("POST", f"{base}/downloadclient", client, headers=arr_headers(api_key))
    log(f"{kind} download client category={cat} after_download={after_download()} remove={remove}")


def ensure_prowlarr_download_client(prowlarr: str, pkey: str, secrets: dict) -> None:
    """Prowlarr Search → Grab. Separate qbit category so Arr CDH cannot mis-import.

    Arr Interactive Search uses Radarr/Sonarr's own clients (radarr / sonarr).
    Reusing those categories here would let Radarr claim a TV grab. Prowlarr is
    not the importer — leave completed torrents in qbit so housekeep/Arr can
    see the files. Save path is downloads/manual (not complete/, and not an
    Arr library root).
    """
    remove = False
    cat, values = qbit_client_values(secrets, "prowlarr")
    existing = as_list(http("GET", f"{prowlarr}/api/v1/downloadclient", headers=arr_headers(pkey)))
    for item in existing:
        if item.get("implementation") != "QBittorrent" or item.get("id") is None:
            continue
        if download_client_ready(item, cat, remove) and all(app_field(item, k) == v for k, v in values.items() if app_field(item, k) is not None):
            return
        updated = apply_download_client(item, values, remove)
        http(
            "PUT",
            f"{prowlarr}/api/v1/downloadclient/{item['id']}",
            updated,
            headers=arr_headers(pkey),
        )
        log(f"prowlarr download client category={cat} removeCompleted={remove}")
        return
    schema = http("GET", f"{prowlarr}/api/v1/downloadclient/schema", headers=arr_headers(pkey))
    client = apply_download_client(schema_impl(schema, "QBittorrent"), values, remove)
    client["name"] = "qBittorrent"
    client["priority"] = 1
    http("POST", f"{prowlarr}/api/v1/downloadclient", client, headers=arr_headers(pkey))
    log(f"prowlarr download client category={cat} removeCompleted={remove}")


MIN_IMPORT_FREE_SPACE = 100


def ensure_media_management(base: str, api_key: str, kind: str) -> None:
    """Import completed torrents into the library folder Seerr asked for.

    Network media shares (this house: /media/dlna) often report 0 free space, so
    Radarr/Sonarr skip import and the file sits in downloads/complete. Auto
    completed-download handling must stay on. Hardlinks do not work on CIFS, and
    the copy fallback is a slow second write — same-share rename (Move) is the
    household path. Do not fail the wait screen if this PUT 400s — quality
    profiles and Seerr still need to wire.
    """
    try:
        cfg = http("GET", f"{base}/config/mediamanagement", headers=arr_headers(api_key))
    except RuntimeError as exc:
        raise RuntimeError(f"{kind} media management failed") from exc
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{kind} media management missing")
    want = {
        "enableCompletedDownloadHandling": True,
        "skipFreeSpaceCheckWhenImporting": True,
        "minimumFreeSpaceWhenImporting": MIN_IMPORT_FREE_SPACE,
        "copyUsingHardlinks": after_download() != "stop_sharing",
        "importExtraFiles": True,
        "extraFileExtensions": "srt",
    }
    if kind == "sonarr":
        # Native revision preference precedes custom-format scores. Let the
        # guide's Repack CF score corrections so one v2 episode cannot defeat
        # the preference for a consistent season pack.
        want["downloadPropersAndRepacks"] = "doNotPrefer"
    # Recycle bin is how Arr upgrades avoid permanently deleting a library file
    # (empty recycleBin + ManualImport/CDH upgrade = dest gone if the move fails).
    extra = {
        "recycleBin": downloads_recycle(),
        "recycleBinCleanupDays": 7,
        "deleteEmptyFolders": False,
        "useScriptImport": False,
        "autoUnmonitorPreviouslyDownloadedEpisodes": False,
        "autoUnmonitorPreviouslyDownloadedMovies": False,
    }
    for key, value in extra.items():
        if key in cfg:
            want[key] = value
    recycle = want.get("recycleBin")
    if recycle:
        try:
            os.makedirs(recycle, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"{kind} recycle folder is not writable") from exc
    if all(cfg.get(key) == value for key, value in want.items()):
        return
    updated = dict(cfg)
    updated.update(want)
    ident = cfg.get("id", 1)
    try:
        http(
            "PUT",
            f"{base}/config/mediamanagement/{ident}",
            updated,
            headers=arr_headers(api_key),
        )
    except RuntimeError as exc:
        raise RuntimeError(f"{kind} media management failed") from exc
    log(
        f"{kind} import into library folders (rename on the NAS, skip free-space check, "
        "old files go to downloads/recycle)"
    )


def ensure_download_client_handling(base: str, api_key: str, kind: str) -> None:
    """Keep completed-download handling on. Do not grab the same title again if
    a torrent was dropped before the file was renamed into the library.
    """
    try:
        cfg = http("GET", f"{base}/config/downloadclient", headers=arr_headers(api_key))
    except RuntimeError as exc:
        raise RuntimeError(f"{kind} download-client configuration failed") from exc
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{kind} download-client configuration missing")
    want = {
        "enableCompletedDownloadHandling": True,
        "autoRedownloadFailed": False,
    }
    if "autoRedownloadFailedFromInteractiveSearch" in cfg:
        want["autoRedownloadFailedFromInteractiveSearch"] = False
    if all(cfg.get(key) == value for key, value in want.items()):
        return
    updated = dict(cfg)
    updated.update(want)
    ident = cfg.get("id", 1)
    try:
        http(
            "PUT",
            f"{base}/config/downloadclient/{ident}",
            updated,
            headers=arr_headers(api_key),
        )
    except RuntimeError as exc:
        raise RuntimeError(f"{kind} download-client configuration failed") from exc
    log(f"{kind} completed-download handling on (no auto re-download)")


def title_library_path(item: dict) -> str:
    path = item.get("path") or item.get("rootFolderPath") or ""
    if isinstance(path, str) and path.strip():
        return path.strip()
    return ""


def arr_title_collection(kind: str) -> str:
    return "movie" if kind == "radarr" else "series"


def recyclarr_marker() -> str:
    return os.path.join(ready_dir(), "recyclarr")


def recyclarr_is_fresh() -> bool:
    """True when Recyclarr already synced recently. Skip a redundant sync."""
    path = recyclarr_marker()
    if not os.path.isfile(path):
        return False
    try:
        max_age = float(env("POMPEY_RECYCLARR_MAX_AGE", "86400") or "86400")
    except ValueError:
        max_age = 86400.0
    try:
        return (time.time() - os.path.getmtime(path)) < max_age
    except OSError:
        return False


def recyclarr_sync_script() -> str:
    override = env("POMPEY_RECYCLARR_SYNC")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.join(here, "recyclarr_sync.py")
    if os.path.isfile(sibling):
        return sibling
    return "/usr/local/bin/recyclarr_sync.py"


def recyclarr_binary() -> str:
    override = env("POMPEY_RECYCLARR")
    if override:
        return override
    root = os.path.join(env("POMPEY_ENGINES", "/data/engines"), "recyclarr")
    nested = os.path.join(root, "recyclarr")
    if os.path.isfile(nested) and os.access(nested, os.X_OK):
        return nested
    return root


def run_recyclarr(*, force: bool = False) -> bool:
    """Sync TRaSH onto Default/Max. Required profiles remain unavailable until a successful sync."""
    if not force and recyclarr_is_fresh():
        return True
    binary = recyclarr_binary()
    if not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        log_if_new(
            "recyclarr-missing",
            "Recyclarr binary not on disk; Default/Max are unavailable until Recyclarr can sync",
        )
        return False
    script = recyclarr_sync_script()
    if not os.path.isfile(script):
        log(
            "Recyclarr sync script missing; Default/Max are unavailable until Recyclarr can sync",
            "WARNING",
        )
        return False
    try:
        proc = subprocess.run(
            [sys.executable, script],
            check=False,
            timeout=int(env("POMPEY_RECYCLARR_TIMEOUT", "600") or "600"),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"Recyclarr: {exc}", "WARNING")
        return False
    if proc.returncode != 0:
        return False
    os.makedirs(ready_dir(), exist_ok=True)
    marker = recyclarr_marker()
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write("ok\n")
    return True


def profile_id_of(item: dict) -> int | None:
    try:
        if item.get("id") is not None:
            return int(item["id"])
    except (TypeError, ValueError):
        return None
    return None


def household_quality_profile(base: str, api_key: str, kind: str):
    """Recyclarr owns Default/Max. Never advertise unconfigured placeholders."""
    profiles = as_list(http("GET", f"{base}/qualityprofile", headers=arr_headers(api_key)))
    names = {row.get("name"): row for row in profiles}
    if not {"Default", "Max"} <= names.keys():
        raise RuntimeError(f"{kind}: waiting for Recyclarr to configure Default and Max")
    template = names["Default"]
    anything = json.loads(json.dumps(names.get("Anything") or template))
    anything.pop("id", None)
    anything.update(name="Anything", upgradeAllowed=False, minFormatScore=0, cutoffFormatScore=0, minUpgradeFormatScore=1)
    def allow(items):
        for item in items:
            item["allowed"] = True
            allow(item.get("items") or [])
    allow(anything.get("items") or [])
    anything["formatItems"] = [{**item, "score": 0} for item in anything.get("formatItems") or []]
    existing = names.get("Anything")
    if existing is None:
        http("POST", f"{base}/qualityprofile", anything, headers=arr_headers(api_key))
    elif any(existing.get(k) != v for k, v in anything.items()):
        http("PUT", f"{base}/qualityprofile/{existing['id']}", anything, headers=arr_headers(api_key))
    return template["id"], "Default"


def language_profile_id(_base: str, _api_key: str):
    # Sonarr v4 dropped language profiles. GET /languageprofile is deprecated and
    # warned on every wire retry (Python-urllib) while doing no work for our 4.0.x.
    return None


def prowlarr_app_values(impl: str, base_url: str, api_key: str) -> dict:
    values = {
        "prowlarrUrl": prowlarr_arr_url(),
        "baseUrl": base_url,
        "apiKey": api_key,
        "syncCategories": sync_cats_for(impl),
    }
    if impl == "Sonarr":
        values[SONARR_ANIME_STANDARD_SEARCH] = True
    return values


def prowlarr_app_matches(existing: dict, values: dict) -> bool:
    if existing.get('syncLevel') != 'fullSync':
        return False
    if str(app_field(existing, 'baseUrl') or '').rstrip('/') != values['baseUrl'].rstrip('/'):
        return False
    if app_field(existing, 'apiKey') not in {values['apiKey'], '********'}:
        return False
    current = str(app_field(existing, "prowlarrUrl") or "").rstrip("/")
    if current != str(values.get("prowlarrUrl") or "").rstrip("/"):
        return False
    if not cats_match(app_field(existing, "syncCategories"), values["syncCategories"]):
        return False
    if SONARR_ANIME_STANDARD_SEARCH in values and not field_is_true(
        existing, SONARR_ANIME_STANDARD_SEARCH
    ):
        return False
    return True


def ensure_prowlarr_app(prowlarr: str, pkey: str, name: str, impl: str, base_url: str, api_key: str) -> None:
    values = prowlarr_app_values(impl, base_url, api_key)
    want = values["prowlarrUrl"]
    want_cats = values["syncCategories"]
    apps = as_list(http("GET", f"{prowlarr}/api/v1/applications", headers=arr_headers(pkey)))
    for existing in apps:
        if existing.get("name") != name:
            continue
        if prowlarr_app_matches(existing, values):
            if app_field(existing, 'apiKey') != '********':
                return
            # Prowlarr masks secrets on GET. Test the stored connection rather
            # than rewriting a healthy one or accepting a broken hidden key.
            try:
                http('POST', f'{prowlarr}/api/v1/applications/test', existing, headers=arr_headers(pkey))
                return
            except RuntimeError:
                pass  # Reapply the managed credentials; PUT validates them.
        if existing.get("id") is None:
            raise RuntimeError(f'Prowlarr app {name} has no ID')
        updated = json.loads(json.dumps(existing))
        updated['syncLevel'] = 'fullSync'
        set_app_fields(updated, values)
        http(
            "PUT",
            f"{prowlarr}/api/v1/applications/{existing['id']}",
            updated,
            headers=arr_headers(pkey),
        )
        log(f"prowlarr app {name} Arr URL {want} cats {want_cats[0]}s")
        return
    schema = http("GET", f"{prowlarr}/api/v1/applications/schema", headers=arr_headers(pkey))
    app = schema_impl(schema, impl)
    set_app_fields(app, values)
    app["name"] = name
    app["syncLevel"] = "fullSync"
    app["tags"] = []
    http("POST", f"{prowlarr}/api/v1/applications", app, headers=arr_headers(pkey))
    log(f"prowlarr app {name}")


def ensure_indexer(prowlarr: str, pkey: str) -> None:
    if not indexer_url():
        log("no source URL configured yet")
        return
    indexers = as_list(http("GET", f"{prowlarr}/api/v1/indexer", headers=arr_headers(pkey)))
    if indexers:
        return
    schema = http("GET", f"{prowlarr}/api/v1/indexer/schema", headers=arr_headers(pkey))
    impl = "Newznab" if "newznab" in indexer_url().lower() else "Torznab"
    try:
        indexer = schema_impl(schema, impl)
    except RuntimeError:
        indexer = schema_impl(schema, "Newznab" if impl == "Torznab" else "Torznab")
    fill_fields(
        indexer,
        {
            "baseUrl": indexer_url().rstrip("/"),
            "apiPath": "/api",
            "apiKey": indexer_api_key(),
        },
    )
    indexer["name"] = "Source"
    indexer["enable"] = True
    indexer["enableRss"] = True
    indexer["enableAutomaticSearch"] = True
    indexer["enableInteractiveSearch"] = True
    indexer["priority"] = 25
    indexer["appProfileId"] = 1
    indexer["tags"] = []
    http("POST", f"{prowlarr}/api/v1/indexer", indexer, headers=arr_headers(pkey))
    log("added source")


def qbit_category(name: str, save_path: str) -> None:
    data = urllib.parse.urlencode({"category": name, "savePath": save_path}).encode()
    try:
        http("POST", f"{qbit_url()}/api/v2/torrents/createCategory", data)
        log(f"qbit category {name} -> {save_path}")
    except RuntimeError as exc:
        # 409 = category already exists. Point it at the requested save path.
        if "409" in str(exc) or "already" in str(exc).lower():
            http("POST", f"{qbit_url()}/api/v2/torrents/editCategory", data)
            return
        raise


def each_arr(radarr: str, rk: str, sonarr: str, sk: str):
    return (("radarr", radarr, rk), ("sonarr", sonarr, sk))


class Seerr:
    def __init__(self, base: str | None = None):
        self.base = (base or seerr_url()).rstrip("/")
        self.cookie = ""
        self.api_key = ""

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def call(self, method: str, path: str, body=None):
        req = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                cookies = []
                if hasattr(resp.headers, "get_all"):
                    cookies = resp.headers.get_all("Set-Cookie") or []
                elif resp.headers.get("Set-Cookie"):
                    cookies = [resp.headers.get("Set-Cookie")]
                for cookie in cookies:
                    self.cookie = cookie.split(";", 1)[0]
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
            raise RuntimeError(f"seerr {method} {path} -> {exc.code} {err[:400]}") from exc


def parse_plex(url: str):
    u = urllib.parse.urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 32400)
    ssl = u.scheme == "https"
    return host, port, ssl


def pick_plex_server(servers, host: str):
    candidates = []
    for srv in as_list(servers):
        provides = srv.get("provides") or []
        if isinstance(provides, str):
            provides = [provides]
        if "server" not in provides and provides:
            continue
        candidates.append(srv)
    owned = [s for s in candidates if s.get("owned")]
    pool = owned or candidates
    host_l = (host or "").lower()
    for srv in pool:
        for conn in as_list(srv.get("connection") or srv.get("connections")):
            addr = (conn.get("address") or "").lower()
            if addr == host_l:
                return srv
    return pool[0] if pool else None


def configure_seerr(secrets: dict, radarr_profile, sonarr_profile, sonarr_lang) -> None:
    s = Seerr()
    for _ in range(wait_tries()):
        try:
            s.call("GET", "/api/v1/settings/public")
            break
        except Exception:  # noqa: BLE001
            time.sleep(wait_sleep())
    else:
        raise RuntimeError("Seerr never became ready")

    s.api_key = seerr_api_key_from_disk()
    have_plex = bool(plex_url() and plex_token())
    if not have_plex:
        log("Plex URL/token missing; Seerr setup wizard will be used for Plex")

    if have_plex:
        try:
            s.call("POST", "/api/v1/auth/plex", {"authToken": plex_token()})
            log("seerr signed in with Plex token")
        except RuntimeError as exc:
            log(f"seerr plex login: {exc}", "WARNING")
            have_plex = False

    email = secrets.get("seerr_email") or "pompey@local"
    password = secrets.get("seerr_password") or ""
    # Disk API key is admin. POST /auth/local with the generated pompey@local
    # password 403s and Seerr logs "invalid Seerr password" on every wire retry.
    if password and not s.cookie and not s.api_key:
        try:
            s.call(
                "POST",
                "/api/v1/auth/local",
                {"email": email, "password": password, "name": "Pompey"},
            )
            log("seerr signed in with local account")
        except RuntimeError as exc:
            # Real Seerr: no local user until the wizard (or a later password). 403 is expected.
            log(f"seerr local login: {exc}", "WARNING")

    if not s.api_key:
        try:
            main = s.call("GET", "/api/v1/settings/main") or {}
            if isinstance(main, dict) and main.get("apiKey"):
                s.api_key = main["apiKey"]
        except RuntimeError as exc:
            log(f"seerr api key: {exc}", "WARNING")
        if not s.api_key:
            s.api_key = seerr_api_key_from_disk()

    def public_initialized() -> bool:
        public = s.call("GET", "/api/v1/settings/public") or {}
        return isinstance(public, dict) and bool(public.get("initialized"))

    initialized = public_initialized()

    if have_plex:
        host, port, ssl = parse_plex(plex_url())
        try:
            servers = as_list(s.call("GET", "/api/v1/settings/plex/devices/servers"))
            chosen = pick_plex_server(servers, host)
            payload = {
                "ip": host,
                "port": port,
                "useSsl": ssl,
                "webAppUrl": plex_url().rstrip("/"),
            }
            if chosen:
                payload["name"] = chosen.get("name") or "Plex"
                payload["machineId"] = chosen.get("clientIdentifier") or ""
            else:
                payload["name"] = "Plex"
                payload["machineId"] = "unknown"
            s.call("POST", "/api/v1/settings/plex", payload)
            log("plex settings")
        except RuntimeError as exc:
            log(f"plex settings: {exc}", "WARNING")

        try:
            s.call("POST", "/api/v1/settings/plex/library/sync")
            libs = as_list(s.call("GET", "/api/v1/settings/plex/library"))
            for lib in libs:
                lib_id = lib.get("id")
                if not lib_id:
                    continue
                try:
                    s.call("PUT", f"/api/v1/settings/plex/library/{lib_id}", {"enabled": True})
                except RuntimeError as exc:
                    log(f"plex library {lib_id}: {exc}", "WARNING")
            log("plex libraries enabled")
        except RuntimeError as exc:
            log(f"plex libraries: {exc}", "WARNING")

    radarr_id, radarr_name = radarr_profile
    sonarr_id, sonarr_name = sonarr_profile
    radarr_payload = {
        "name": "Radarr",
        "hostname": "127.0.0.1",
        "port": 7878,
        "apiKey": secrets["radarr_api_key"],
        "useSsl": False,
        "activeProfileId": radarr_id,
        "activeProfileName": radarr_name,
        "activeDirectory": movies_auto_dir(),
        "isDefault": True,
        "is4k": False,
        "minimumAvailability": "released",
        # Plex scan marks available. Arr library-scan declines in-flight requests.
        "syncEnabled": False,
        "preventSearch": False,
        "tagRequests": True,
        "urlBase": "",
        "externalUrl": "",
        "tags": [],
    }
    sonarr_payload = {
        "name": "Sonarr",
        "hostname": "127.0.0.1",
        "port": 8989,
        "apiKey": secrets["sonarr_api_key"],
        "useSsl": False,
        "activeProfileId": sonarr_id,
        "activeProfileName": sonarr_name,
        "activeDirectory": tv_auto_dir(),
        "activeAnimeDirectory": tv_auto_dir(),
        "isDefault": True,
        "is4k": False,
        "enableSeasonFolders": True,
        "syncEnabled": False,
        "preventSearch": False,
        "tagRequests": True,
        "urlBase": "",
        "externalUrl": "",
        "tags": [],
    }
    if sonarr_lang is not None:
        sonarr_payload["activeLanguageProfileId"] = sonarr_lang

    def upsert_arr(kind: str, payload: dict) -> bool:
        path = f"/api/v1/settings/{kind}"
        try:
            existing = as_list(s.call("GET", path))
            match = next(
                (item for item in existing if item.get("hostname") == "127.0.0.1"),
                None,
            )
            if match:
                want = (payload.get("activeDirectory") or "").rstrip("/")
                have = (match.get("activeDirectory") or "").rstrip("/")
                want_anime = (payload.get("activeAnimeDirectory") or "").rstrip("/")
                have_anime = (match.get("activeAnimeDirectory") or "").rstrip("/")
                server_id = match.get("id")
                dir_changed = bool(want and want != have) or bool(
                    want_anime and want_anime != have_anime
                )
                profile_changed = (
                    match.get("activeProfileId") != payload.get("activeProfileId")
                    or (match.get("activeProfileName") or "") != (payload.get("activeProfileName") or "")
                )
                # Seerr's Radarr/Sonarr scan declines in-flight requests as
                # "orphaned" when the add has not landed yet. Availability
                # comes from Plex; keep Arr library-scan off.
                sync_changed = match.get("syncEnabled") != payload.get("syncEnabled")
                if server_id is not None and any(match.get(k) != v for k, v in payload.items()):
                    update = dict(match)
                    update.update(payload)
                    # Seerr OpenAPI marks id read-only; it belongs in the URL.
                    update.pop("id", None)
                    s.call("PUT", f"{path}/{server_id}", update)
                    if dir_changed:
                        log(f"seerr {kind} directory {want}")
                    if profile_changed:
                        log(f"seerr {kind} profile {payload.get('activeProfileName')}")
                    if sync_changed:
                        log(f"seerr {kind} library scan off")
                return True
            s.call("POST", path, payload)
            log(f"seerr {kind}")
            return True
        except RuntimeError as exc:
            # API key impersonates user id 1. That row does not exist until the
            # wizard creates the first admin, so settings 403. Hand off anyway.
            if "403" in str(exc) and not initialized:
                log(f"seerr {kind} waits until the Plex wizard creates the first admin")
                return False
            if "403" in str(exc):
                raise RuntimeError("seerr has no Radarr/Sonarr after initialize") from exc
            raise

    wired_arr = upsert_arr("radarr", radarr_payload)
    wired_sonarr = upsert_arr("sonarr", sonarr_payload)
    wired_arr = wired_arr and wired_sonarr
    if wired_arr:
        open(os.path.join(ready_dir(), "seerr-arr"), "w").close()
    elif initialized:
        raise RuntimeError("seerr has no Radarr/Sonarr after initialize")

    def ensure_settings(path, desired):
        existing = s.call("GET", path) or {}
        if any(existing.get(k) != v for k, v in desired.items()):
            s.call("POST", path, desired)

    try:
        ensure_settings("/api/v1/settings/main", {
            "applicationTitle": "Pompey", "applicationUrl": "",
            "defaultPermissions": SEERR_HOUSEHOLD_PERMS, "hideAvailable": False,
            "localLogin": True, "newPlexLogin": True,
        })
    except RuntimeError as exc:
        log(f"seerr main settings: {exc}", "WARNING")

    try:
        users = s.call("GET", "/api/v1/user") or {}
        rows = as_list(users)
        if isinstance(users, dict) and not rows:
            rows = as_list(users.get("results") or users.get("users") or [])
        for user in rows:
            uid = user.get("id")
            if uid is None:
                continue
            update = seerr_permission_update(user)
            if update is None:
                continue
            try:
                s.call("PUT", f"/api/v1/user/{uid}", update)
            except RuntimeError as exc:
                log(f"seerr user {uid} permissions: {exc}", "WARNING")
    except RuntimeError as exc:
        log(f"seerr users: {exc}", "WARNING")

    try:
        ensure_settings("/api/v1/settings/network", {"trustProxy": False})
    except RuntimeError as exc:
        log(f"seerr network: {exc}", "WARNING")

    public = s.call("GET", "/api/v1/settings/public") or {}
    already = isinstance(public, dict) and public.get("initialized")
    if already:
        return
    if have_plex:
        s.call("POST", "/api/v1/settings/initialize")
        log("seerr initialized")
    else:
        log("seerr left uninitialized so the Plex setup wizard can run")


def main() -> int:
    secrets = load_secrets()
    wait_http(f"{qbit_url()}/api/v2/app/version")
    wait_http(f"{prowlarr_url()}/ping")
    wait_http(f"{sonarr_url()}/ping")
    wait_http(f"{radarr_url()}/ping")

    sk, rk, pk = secrets["sonarr_api_key"], secrets["radarr_api_key"], secrets["prowlarr_api_key"]
    sonarr = arr_api_root(sonarr_url(), sk)
    radarr = arr_api_root(radarr_url(), rk)
    prowlarr = prowlarr_url()

    complete = downloads_complete()
    manual = downloads_manual()
    for folder in (complete, manual):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Could not create download folder {folder}") from exc
    apply_qbit_queue()
    qbit_category("sonarr", complete)
    qbit_category("radarr", complete)
    qbit_category("prowlarr", manual)
    notices = sync_root_folders(sonarr, sk, "sonarr")
    notices += sync_root_folders(radarr, rk, "radarr")
    persist.save("arr-roots", {"notices": notices})
    ensure_download_client(sonarr, sk, secrets, "sonarr")
    ensure_download_client(radarr, rk, secrets, "radarr")
    ensure_prowlarr_download_client(prowlarr, pk, secrets)
    # Never accept legacy placeholders or a failed sync as configured profiles.
    if not run_recyclarr():
        raise RuntimeError("Recyclarr has not successfully configured quality profiles")
    sp = household_quality_profile(sonarr, sk, "sonarr")
    rp = household_quality_profile(radarr, rk, "radarr")
    for kind, base, key in each_arr(radarr, rk, sonarr, sk):
        ensure_media_management(base, key, kind)
        ensure_download_client_handling(base, key, kind)
    ensure_prowlarr_app(prowlarr, pk, "Sonarr", "Sonarr", sonarr_url(), sk)
    ensure_prowlarr_app(prowlarr, pk, "Radarr", "Radarr", radarr_url(), rk)
    ensure_indexer(prowlarr, pk)

    sl = language_profile_id(sonarr, sk)

    os.makedirs(ready_dir(), exist_ok=True)
    open(os.path.join(ready_dir(), "arr-wired"), "w").close()

    configure_seerr(secrets, rp, sp, sl)
    port = os.environ.get("SEERR_PORT", "5055")
    sources = os.environ.get("PROWLARR_PORT", "9696")
    log(f"search is on 0.0.0.0:{port}; sources on 0.0.0.0:{sources}; Ingress stays Pompey")
    # Marker first so a concurrent fetch/vpn writer cannot rewind the bar.
    open(os.path.join(ready_dir(), "wired"), "w").close()
    mark_status("ready", "Ready", 100)
    log("done")
    return 0


def housekeep() -> int:
    from media_policy import maintain_downloads
    maintain_downloads()
    return 0


def closeout() -> int:
    from request_policy import reconcile_requests
    reconcile_requests()
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "housekeep":
            raise SystemExit(housekeep())
        if len(sys.argv) > 1 and sys.argv[1] == "closeout":
            raise SystemExit(closeout())
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        log(str(exc), "ERROR")
        # Re-wire after an engine swap must not flip the wait screen off search.
        mark_status("wire", "Could not connect search", 80, "Retrying. Check the app log for details.")
        raise SystemExit(1)
